from django.shortcuts import render, redirect
from .models import BaseUser, Trader, MarketMaker, Order, Trade, Stoploss_Order, MarketControl
from django.db.models import Q
from django.db import transaction
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import ensure_csrf_cookie
import json
import logging
import re
import csv
import io
from django.contrib import messages
from .utils import broadcast_orderbook_update  # Assuming broadcast_orderbook_update is in utils.py
from django.contrib.auth import logout as auth_logout

from .utils import match_order  # Assuming match_order is in utils.py
from django.http import JsonResponse

from django.contrib.auth.models import User as AuthUser

logger = logging.getLogger(__name__)


def _visible_disclosed(order):
    if not order:
        return 0
    peak_disclosed = max(int(order.disclosed or 0), 0)
    quantity = max(int(order.quantity or 0), 0)
    original_quantity = max(int(order.original_quantity or 0), 0)

    if peak_disclosed <= 0:
        return quantity
    if quantity <= 0:
        return 0

    filled = max(original_quantity - quantity, 0)
    consumed_in_current_tranche = filled % peak_disclosed
    current_tranche_visible = peak_disclosed if consumed_in_current_tranche == 0 else (peak_disclosed - consumed_in_current_tranche)

    return min(quantity, current_tranche_visible)


def _serialize_order(order):
    return {
        'user': order.user_id,
        'price': order.price,
        'disclosed': _visible_disclosed(order),
        'is_matched': order.is_matched,
        'id': order.id,
        'is_ioc': order.is_ioc,
        'quantity': order.quantity,
        'original_quantity': order.original_quantity,
    }

def login(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        user_type = request.POST.get('user_type')

        if user_type == 'TRADER':
            user, created = Trader.objects.get_or_create(username=username,role="TRADER")
            return redirect('trader_home', user_id=user.id)
        else:
            user, created = MarketMaker.objects.get_or_create(username=username,role="MARKET_MAKER")
            return redirect('market_maker_home', user_id=user.id)

        
    return render(request, 'trading/login.html')


def logout_view(request):
    auth_logout(request)
    return redirect('login')


def _get_or_create_base_user(auth_user):
    try:
        return BaseUser.objects.get(username=auth_user.username)
    except BaseUser.DoesNotExist:
        if auth_user.is_superuser:
            return BaseUser.objects.create(username=auth_user.username, role='ADMIN')
    return None


def _is_admin(auth_user):
    from django.contrib.auth.models import User
    try:
        fresh = User.objects.get(pk=auth_user.pk)
        return fresh.is_superuser
    except User.DoesNotExist:
        return False


@login_required
def role_router(request):
    auth_user = request.user
    base_user = _get_or_create_base_user(auth_user)
    if not base_user:
        messages.error(request, 'Account role is missing. Please contact an admin.')
        return redirect('login')

    if base_user.role == 'ADMIN':
        if auth_user.is_superuser:
            return redirect('admin_home')
        messages.error(request, 'Admin access requires a superuser account.')
        return redirect('login')
    if base_user.role == 'MARKET_MAKER':
        return redirect('mm_home')
    if base_user.role == 'TRADER':
        return redirect('trader_home')
    return redirect('admin_home')


@login_required
@ensure_csrf_cookie
def admin_home(request):
    if not _is_admin(request.user):
        return redirect('role_router')

    best_bid = fetch_best_bid()
    best_ask = fetch_best_ask()

    best_bid_price = float(best_bid['price']) if best_bid and best_bid.get('price') is not None else None
    best_ask_price = float(best_ask['price']) if best_ask and best_ask.get('price') is not None else None
    spread = None
    if best_bid_price is not None and best_ask_price is not None and best_ask_price >= best_bid_price:
        spread = best_ask_price - best_bid_price

    recent_trades = Trade.objects.select_related('buyer', 'seller').order_by('-timestamp')[:10]

    context = {
        'base_role': 'ADMIN',
        'trader_count': BaseUser.objects.filter(role='TRADER').count(),
        'market_maker_count': BaseUser.objects.filter(role='MARKET_MAKER').count(),
        'active_limit_orders': Order.objects.filter(order_mode='LIMIT', is_matched=False).count(),
        'active_stoploss_orders': Stoploss_Order.objects.filter(is_matched=False).count(),
        'trades_today': Trade.objects.filter(timestamp__date=timezone.now().date()).count(),
        'total_trades': Trade.objects.count(),
        'best_bid_price': best_bid_price,
        'best_ask_price': best_ask_price,
        'spread': spread,
        'best_bid_disclosed': best_bid['disclosed'] if best_bid else None,
        'best_ask_disclosed': best_ask['disclosed'] if best_ask else None,
        'last_trade': Trade.objects.order_by('-timestamp').first(),
        'recent_trades': recent_trades,
    }

    return render(request, 'trading/admin.html', context)


@login_required
def get_market_status(request):
    if request.method == 'GET':
        try:
            mc = MarketControl.objects.first()
            paused = mc.paused if mc else False
            message = mc.message if mc else ''
        except Exception:
            paused = False
            message = ''
        return JsonResponse({'paused': paused, 'message': message})
    return JsonResponse({'paused': False, 'message': ''}, status=405)


@login_required
def toggle_market_pause(request):
    if not _is_admin(request.user):
        return JsonResponse({'success': False, 'message': 'Admin access required.'}, status=403)
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            action = data.get('action')
            message = data.get('message', '')
            mc, _ = MarketControl.objects.get_or_create(id=1)
            if action == 'pause':
                mc.paused = True
                mc.message = message
            else:
                mc.paused = False
                mc.message = ''
            mc.save()

            # Try broadcasting to websocket group if channels are configured
            try:
                from asgiref.sync import async_to_sync
                from channels.layers import get_channel_layer
                channel_layer = get_channel_layer()
                async_to_sync(channel_layer.group_send)(
                    'orderbook_group',
                    {
                        'type': 'send_order_update',
                        'payload': {
                            'event': 'market_pause',
                            'paused': mc.paused,
                            'message': mc.message,
                        }
                    }
                )
            except Exception:
                pass

            return JsonResponse({'success': True, 'paused': mc.paused, 'message': mc.message})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)}, status=500)
    return JsonResponse({'success': False}, status=405)
def fetch_best_ask():
    order = Order.objects.filter(
        order_type="SELL",
        order_mode="LIMIT",
        price__isnull=False,
        is_matched=False,
    ).order_by('price').first()
    if not order:
        return None
    return {'price': order.price, 'disclosed': _visible_disclosed(order)}

def fetch_best_bid():
    order = Order.objects.filter(
        order_type="BUY",
        order_mode="LIMIT",
        price__isnull=False,
        is_matched=False,
    ).order_by('-price').first()
    if not order:
        return None
    return {'price': order.price, 'disclosed': _visible_disclosed(order)}

@login_required
def get_best_ask(request):
    if request.method == 'GET':
        best_ask_order = Order.objects.filter(
            order_type="SELL",
            order_mode="LIMIT",
            price__isnull=False,
            is_matched=False,
        ).order_by('price').first()
        best_ask = None
        if best_ask_order:
            best_ask = {'price': best_ask_order.price, 'disclosed': _visible_disclosed(best_ask_order)}
        return JsonResponse({'best_ask': best_ask})
    return JsonResponse({'best_ask': None})

@login_required
def get_best_bid(request):
    if request.method == 'GET':
        best_bid_order = Order.objects.filter(
            order_type="BUY",
            order_mode="LIMIT",
            price__isnull=False,
            is_matched=False,
        ).order_by('-price').first()
        best_bid = None
        if best_bid_order:
            best_bid = {'price': best_bid_order.price, 'disclosed': _visible_disclosed(best_bid_order)}
        return JsonResponse({'best_bid': best_bid})
    return JsonResponse({'best_bid': None})

@login_required  # Ensure the user is logged in before accessing this view
def market_maker_home(request):
    auth_user = request.user
    user = _get_or_create_base_user(auth_user)

    if not user or user.role != "MARKET_MAKER":
        return redirect('role_router')

    if request.method == "POST":
        # Block new orders if market is paused
        mc = MarketControl.objects.first()
        if mc and mc.paused:
            return JsonResponse({'success': False, 'message': 'Market activity is paused: ' + (mc.message or 'No reason provided.')}, status=403)
        try:
            order_type = request.POST.get('order_type')
            order_mode = 'LIMIT'
            quantity = int(request.POST.get('quantity', 0))
            disclosed = int(request.POST.get('disclosed_quantity', 0))
            paired_quantity = request.POST.get('paired_quantity')
            stoploss_order = request.POST.get('Stoploss_order', 'NO')
            target_price = request.POST.get('Target_price')
            is_ioc = request.POST.get('is_ioc') == 'True'
            original_quantity = quantity
            end_time = request.POST.get('end_time')

            print(f"[DEBUG] Market Maker Order: type={order_type}, qty={quantity}, disclosed={disclosed}, stoploss={stoploss_order}")

            # Validate basic fields
            if not order_type or quantity <= 0:
                return JsonResponse({'success': False, 'message': 'Invalid order type or quantity'}, status=400)

            if paired_quantity not in (None, ''):
                try:
                    paired_quantity_value = int(paired_quantity)
                except (TypeError, ValueError):
                    return JsonResponse({'success': False, 'message': 'Invalid paired quantity value.'}, status=400)

                if paired_quantity_value <= 0:
                    return JsonResponse({'success': False, 'message': 'Paired quantity must be greater than 0.'}, status=400)

                if paired_quantity_value != quantity:
                    return JsonResponse(
                        {'success': False, 'message': 'Bid and Ask quantities must be the same for market maker quotes.'},
                        status=400,
                    )

            # Calculate minimum disclosed (10% of quantity, minimum 1)
            min_disclosed = max(1, int(quantity * 0.1))
            
            # Set disclosed to full quantity if not specified or 0
            if disclosed == 0:
                disclosed = quantity
            
            # Ensure disclosed doesn't exceed quantity
            if disclosed > quantity:
                disclosed = quantity
            
            # Auto-correct if disclosed is slightly below minimum (within 1 unit)
            if disclosed < min_disclosed and disclosed >= min_disclosed - 1:
                disclosed = min_disclosed
                print(f"[DEBUG] Auto-corrected disclosed from {disclosed} to {min_disclosed}")
            
            # Validate disclosed is at least 10% of quantity (for iceberg orders)
            if disclosed < min_disclosed:
                return JsonResponse({
                    'success': False,
                    'message': f'Disclosed quantity too small. Need at least {min_disclosed}, got {disclosed}. Minimum is 10% of order quantity.'
                }, status=400)

            # Handle price parsing for LIMIT orders
            try:
                price = float(request.POST.get('price', 0)) if order_mode == "LIMIT" else None
            except (ValueError, TypeError):
                return JsonResponse({'success': False, 'message': 'Invalid price format'}, status=400)

            if order_mode == "LIMIT" and (price is None or price <= 0):
                return JsonResponse({'success': False, 'message': 'Valid price required for limit orders'}, status=400)

            # Handle stoploss orders
            if stoploss_order != 'NO':
                try:
                    if target_price in (None, ''):
                        return JsonResponse(
                            {'success': False, 'message': 'Trigger price is required for stoploss orders.'},
                            status=400,
                        )

                    try:
                        parsed_target_price = float(target_price)
                    except (TypeError, ValueError):
                        return JsonResponse(
                            {'success': False, 'message': 'Invalid trigger price format.'},
                            status=400,
                        )

                    if parsed_target_price <= 0:
                        return JsonResponse(
                            {'success': False, 'message': 'Trigger price must be greater than 0.'},
                            status=400,
                        )

                    new_order = Stoploss_Order(
                        order_type=order_type,
                        order_mode=order_mode,
                        quantity=quantity,
                        disclosed=disclosed,
                        target_price=parsed_target_price,
                        price=price,
                        is_matched=False,
                        is_ioc=is_ioc,
                        user=user,
                    )
                    new_order.save()
                    broadcast_orderbook_update()
                    print(f"[DEBUG] Stoploss order saved: ID={new_order.id}")
                    return JsonResponse({'success': True, 'message': 'Stoploss order placed successfully!'})
                except Exception as e:
                    print(f"[DEBUG] Stoploss order error: {str(e)}")
                    return JsonResponse({'success': False, 'message': f'Error saving stoploss order: {str(e)}'}, status=500)

            # Handle regular LIMIT orders
            try:
                with transaction.atomic():
                    new_order = Order(
                        order_type=order_type,
                        order_mode=order_mode,
                        quantity=quantity,
                        disclosed=disclosed,
                        price=price,
                        is_matched=False,
                        is_ioc=is_ioc,
                        user=user,
                        user_role=user.role,
                        original_quantity=original_quantity
                    )
                    new_order.save()
                    print(f"[DEBUG] Order saved: ID={new_order.id}, Type={order_type}, Qty={quantity}, Disclosed={disclosed}, Price={price}")
                    broadcast_orderbook_update()
                    
                    # Market maker orders are passive - they rest on the book
                    # and get matched only when incoming trader orders cross them.
                    # Do NOT call match_order here.
                    
                    return JsonResponse({'success': True, 'message': f'Order placed: {order_type} {quantity}@{price}'})
            except Exception as e:
                print(f"[DEBUG] Order save error: {str(e)}")
                return JsonResponse({'success': False, 'message': f'Error saving order: {str(e)}'}, status=500)

        except ValueError as e:
            print(f"[DEBUG] ValueError: {str(e)}")
            return JsonResponse({'success': False, 'message': f'Invalid input: {str(e)}'}, status=400)
        except Exception as e:
            print(f"[DEBUG] Unexpected error: {str(e)}")
            return JsonResponse({'success': False, 'message': f'Unexpected error: {str(e)}'}, status=500)
        


    # Fetch orders associated with the user
    orders = Order.objects.filter(user=user)  # Filter orders by the logged-in user
    trades = Trade.objects.filter(Q(buyer=user) | Q(seller=user))
    # changes:
    stoploss_orders = Stoploss_Order.objects.filter(user=user)

    execute_order()
    return render(request, 'trading/market-maker.html', {'orders': orders, 'trades': trades, 'stoploss_orders': stoploss_orders, 'base_role': user.role})

@login_required
def trader_home(request):
    auth_user = request.user
    user = _get_or_create_base_user(auth_user)

    if not user or user.role != "TRADER":
        return redirect('role_router')
    
    if request.method == "POST":
        # Block new orders if market is paused
        mc = MarketControl.objects.first()
        if mc and mc.paused:
            return JsonResponse({'success': False, 'message': 'Market activity is paused: ' + (mc.message or 'No reason provided.')}, status=403)
        order_type = request.POST.get('order_type')
        order_mode = "MARKET"
        quantity = int(request.POST.get('quantity'))
        disclosed = int(request.POST.get('disclosed_quantity'))
        stoploss_order =  "NO"
        target_price = None
        is_ioc=request.POST.get('is_ioc')=='True'
        original_quantity=quantity

        price = None
        end_time=request.POST.get('end_time')

        if disclosed==0:
            disclosed=quantity

        try:

            if order_mode == "MARKET":
                if order_type == "BUY":
                    # Fetch the JSON response from the best ask view
                    best_ask_response = fetch_best_ask()
                    best_ask_data=best_ask_response
                    best_price = best_ask_data['price'] if best_ask_data else None

                elif order_type == "SELL":
                    # Fetch the JSON response from the best bid view
                    best_bid_response = fetch_best_bid()
                    best_bid_data=best_bid_response
                    best_price = best_bid_data['price'] if best_bid_data else None

                if best_price is None:
                    return render(request, 'trading/trader.html', {'error': 'Unable to fetch market price for the order type.'})
                    # Create and save the new order

            if disclosed>quantity:
                disclosed=quantity

            if(stoploss_order=='NO' or stoploss_order==None):
                    # Save or process the order here
                new_order = Order(
                    order_type=order_type,
                    order_mode=order_mode,
                    quantity=quantity,
                    disclosed=disclosed,
                    price=None,
                    is_matched=False,
                    is_ioc=is_ioc,
                    user=user,  # Ensure the order is associated with the logged-in user
                    user_role=user.role,
                    original_quantity=original_quantity

                )

                if disclosed < 0.1 * quantity:  # disclosed_quantity should not be > 10% of quantity
                    messages.error(request, "Disclosed Quantity cannot be less than 10% greater than Quantity.")

                else:
                    # Proceed with saving the order or further logic
                    messages.success(request, "Order placed successfully!")
                    print("I am here")
                    try:
                        new_order.save()
                        print("Order saved!")
                        broadcast_orderbook_update()
                        print("call1")
                        match_order(new_order)
                        messages.success(request, 'Your order has been placed successfully!')
                    except Exception as e:
                        print("Error saving order:", e)
                        messages.error(request, f"Order could not be saved: {e}")
                    return redirect('trader_home')

            else:
                new_order = Stoploss_Order (
                    order_type=order_type,
                    order_mode=order_mode,
                    quantity=quantity,
                    disclosed=disclosed,
                    target_price=target_price,
                    price=price,
                    is_matched=False,
                    is_ioc=is_ioc,
                    user=user,
                )
                broadcast_orderbook_update()

                if disclosed < 0.1 * quantity:  # disclosed_quantity should not be > 10% of quantity
                    messages.error(request, "Disclosed Quantity cannot be less than 10% greater than Quantity.")

                else:
                    # Proceed with saving the order or further logic
                    messages.success(request, "Stoploss Order placed successfully!")
                    new_order.save()
                    broadcast_orderbook_update()
                    messages.success(request, 'Your Stoploss order has been placed successfully!')
                    return redirect('trader_home')


        except Exception as e:
            return render(request, 'trading/trader.html', {'error': 'Unable to fetch market price for the order type.'})
        


    # Fetch orders associated with the user
    orders = Order.objects.filter(user=user)  # Filter orders by the logged-in user
    trades = Trade.objects.filter(Q(buyer=user) | Q(seller=user))
    # changes:
    stoploss_orders = Stoploss_Order.objects.filter(user=user)

    execute_order()
    return render(request, 'trading/trader.html', {'orders': orders, 'trades': trades, 'stoploss_orders': stoploss_orders, 'base_role': user.role})


@login_required
def orderbook(request):
    base_user = _get_or_create_base_user(request.user)
    # Retrieve unmatched buy orders (sorted by price in descending order)
    # Show only disclosed quantity for iceberg orders
    buy_orders = Order.objects.filter(
        is_matched=False,
        order_type='BUY',
        order_mode='LIMIT',
        price__isnull=False,
    ).order_by('-price')
    
    # Retrieve unmatched sell orders (sorted by price in ascending order)
    # Show only disclosed quantity for iceberg orders
    sell_orders = Order.objects.filter(
        is_matched=False,
        order_type='SELL',
        order_mode='LIMIT',
        price__isnull=False,
    ).order_by('price')
    
    # Retrieve all trades (you may filter or sort as needed)
    trades = Trade.objects.all().order_by('-timestamp')  # Sorting trades by timestamp
  
    # Display both buy and sell orders in the orderbook, along with trades
    # The template will use order.disclosed for display (iceberg visible qty) and order.quantity for actual qty
    return render(request, 'trading/orderbook.html', {
        'buy_orders': buy_orders,
        'sell_orders': sell_orders,
        'best_bid': buy_orders.first() if buy_orders else None,
        'best_ask': sell_orders.first() if sell_orders else None,
        'trades': trades,  # Pass trades to the template
        'base_role': base_user.role if base_user else None,
    })

@login_required
def modify(request):
    if not _is_admin(request.user):
        return redirect('role_router')
    base_user = _get_or_create_base_user(request.user)
    # Retrieve unmatched buy orders (sorted by price in descending order)
    buy_orders = Order.objects.filter(
        is_matched=False,
        order_type='BUY',
        order_mode='LIMIT',
        price__isnull=False,
    ).order_by('-price')
    # Retrieve unmatched sell orders (sorted by price in ascending order)
    sell_orders = Order.objects.filter(
        is_matched=False,
        order_type='SELL',
        order_mode='LIMIT',
        price__isnull=False,
    ).order_by('price')
    
    # Retrieve all trades (you may filter or sort as needed)
    trades = Trade.objects.all().order_by('-timestamp')  # Sorting trades by timestamp
    
    # Display both buy and sell orders in the orderbook, along with trades
    return render(request, 'trading/modify.html', {
        'buy_orders': buy_orders,
        'sell_orders': sell_orders,
        'best_bid': buy_orders.first() if buy_orders else None,
        'best_ask': sell_orders.first() if sell_orders else None,
        'trades': trades,  # Pass trades to the template
        'base_role': base_user.role if base_user else None,
    })

@login_required  
def modify_order_page(request):
    if not _is_admin(request.user):
        return redirect('role_router')
    base_user = _get_or_create_base_user(request.user)
    # Retrieve unmatched buy orders (sorted by price in descending order)
    buy_orders = Order.objects.filter(
        is_matched=False,
        order_type='BUY',
        order_mode='LIMIT',
        price__isnull=False,
    ).order_by('-price')
    # Retrieve unmatched sell orders (sorted by price in ascending order)
    sell_orders = Order.objects.filter(
        is_matched=False,
        order_type='SELL',
        order_mode='LIMIT',
        price__isnull=False,
    ).order_by('price')
    
    # Retrieve all trades (you may filter or sort as needed)
    trades = Trade.objects.all().order_by('-timestamp')  # Sorting trades by timestamp

    # Display both buy and sell orders in the orderbook, along with trades
    return render(request, 'trading/modify_order.html', {
        'buy_orders': buy_orders,
        'sell_orders': sell_orders,
        'trades': trades,  # Pass trades to the template
        'base_role': base_user.role if base_user else None,
    })
     
@login_required
def update_prev_order(request):
    if not _is_admin(request.user):
        return JsonResponse({'success': False, 'message': 'Admin access required.'}, status=403)
    if request.method == 'POST':
        try:
            # Extract the order_id, quantity, and price from the JSON body
            data = json.loads(request.body)
            order_id = data.get('order_id')
            new_quantity = data.get('quantity')
            new_disclosed = data.get('disclosed_quantity')
            new_price = data.get('price')

            # Validate the order_id, new_quantity, and new_price
            order_id = int(order_id)
            new_quantity = int(new_quantity)
            new_disclosed = int(new_disclosed)
            new_price = float(new_price)
            
            print(f"Received order update: Order ID = {order_id}, Quantity = {new_quantity}, Disclosed Quantity = {new_disclosed}, Price = {new_price}")
              
            # Check if the order exists
            order = Order.objects.get(id=order_id)
            if order.is_matched == True:
                return JsonResponse({'success': False, 'message': 'Order has already been placed. No modifications allowed.'})
            if new_disclosed < new_quantity * 0.1:
                return JsonResponse({'success': False, 'message': 'Disclosed value must be greater then 10% of quantity.'})
            if new_disclosed > new_quantity:
                return JsonResponse({'success': False, 'message': 'Cannot disclose more than the quantity.'})
            if new_price <= 0:
                return JsonResponse({'success': False, 'message': 'Price must be greater than 0.'})
            
            order.quantity = new_quantity
            order.disclosed = new_disclosed
            order.price = new_price
            order.save()
            broadcast_orderbook_update()

            return JsonResponse({'success': True})

        except Order.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Order not found.'})
        except ValueError:
            return JsonResponse({'success': False, 'message': 'Invalid data provided.'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})


@login_required
def clear_database(request):
    if not _is_admin(request.user):
        return redirect('role_router')
    Order.objects.all().delete()
    Trade.objects.all().delete()
    return redirect('login')

@login_required
def get_buy_orders(request):
    if request.method == 'GET':
        buy_orders = Order.objects.filter(
            order_type='BUY',
            order_mode='LIMIT',
            price__isnull=False,
            is_matched=False,
        ).order_by('-price', 'timestamp')
        return JsonResponse({'buy_orders': [_serialize_order(order) for order in buy_orders]})
    return JsonResponse({'buy_orders': []}, status=405)

@login_required
def get_sell_orders(request):
    if request.method == 'GET':
        sell_orders = Order.objects.filter(
            order_type='SELL',
            order_mode='LIMIT',
            price__isnull=False,
            is_matched=False,
        ).order_by('price', 'timestamp')
        return JsonResponse({'sell_orders': [_serialize_order(order) for order in sell_orders]})
    return JsonResponse({'sell_orders': []}, status=405)

@login_required
def get_recent_trades(request):
    if request.method == 'GET':
        recent_trades = Trade.objects.all().order_by('-timestamp')[:10].values(
            'price', 'quantity', 'timestamp'
        )  # Hide buyer and seller information
        return JsonResponse({'trades': list(recent_trades)})
    return JsonResponse({'trades': []}, status=405)


@login_required
def cancel_order(request):
    if request.method == 'POST':
        try:
            logger.debug(f"Cancellation request received: {request.body}")
            # Get current user using the same pattern as order placement
            user = BaseUser.objects.get(username=request.user.username)
            
            data = json.loads(request.body)
            order_id = data.get('order_id')
            
            with transaction.atomic():
                order = Order.objects.get(
                    id=order_id,
                    user=user,
                    is_matched=False
                )
                order.delete()
            
            return JsonResponse({'success': True, 'message': 'Order cancelled successfully'})
        
        except BaseUser.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'User authentication failed'}, status=401)
        except Order.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Order not found or already matched'}, status=404)
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'message': 'Invalid request format'}, status=400)
        except Exception as e:
            logger.error(f"Cancel order error: {str(e)}")
            return JsonResponse({'success': False, 'message': str(e)}, status=500)
        
        
    
    # changes:
    
@login_required
def cancel_stoploss_order(request):
    if request.method == 'POST':
        try:
            logger.debug(f"Stoploss cancellation request received: {request.body}")
            user = BaseUser.objects.get(username=request.user.username)
            
            data = json.loads(request.body)
            order_id = data.get('order_id')
            
            with transaction.atomic():
                order = Stoploss_Order.objects.get(
                    id=order_id,
                    user=user,
                    is_matched=False
                )
                order.delete()
            
            return JsonResponse({'success': True, 'message': 'Stoploss order cancelled successfully'})
        
        except BaseUser.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'User authentication failed'}, status=401)
        except Stoploss_Order.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Order not found or already matched'}, status=404)
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'message': 'Invalid request format'}, status=400)
        except Exception as e:
            logger.error(f"Cancel stoploss order error: {str(e)}")
            return JsonResponse({'success': False, 'message': str(e)}, status=500)

def convert_stoploss_to_order(stoploss_order):
    user_role = stoploss_order.user.role if stoploss_order.user and stoploss_order.user.role else 'MARKET_MAKER'
    return Order(
        user=stoploss_order.user,
        user_role=user_role,
        order_type=stoploss_order.order_type,
        order_mode=stoploss_order.order_mode,
        quantity=stoploss_order.quantity,
        price=stoploss_order.price,
        disclosed=stoploss_order.disclosed,
        original_quantity=stoploss_order.quantity,
        timestamp=timezone.now(),
        is_matched=False,
        is_ioc=stoploss_order.is_ioc,
    )

@transaction.atomic
def execute_order():
    print("was called!")
    last_trade = Trade.objects.last()
    if not last_trade:
        logger.info("No last trade found.")
        return
    
    closing_price = last_trade.price
    logger.info("Last trade price: %s", closing_price)

    stop_loss_buy_orders = Stoploss_Order.objects.filter(order_type='BUY').order_by('target_price')
    stop_loss_sell_orders = Stoploss_Order.objects.filter(order_type='SELL').order_by('-target_price')

    for buy_order in stop_loss_buy_orders:
        if buy_order.target_price is None:
            logger.warning("Skipping BUY stoploss order %s with null target_price", buy_order.id)
            continue
        if buy_order.target_price >= closing_price:
            new_order = convert_stoploss_to_order(buy_order)
            new_order.save()
            match_order(new_order)
            buy_order.delete()

    for sell_order in stop_loss_sell_orders:
        if sell_order.target_price is None:
            logger.warning("Skipping SELL stoploss order %s with null target_price", sell_order.id)
            continue
        if sell_order.target_price <= closing_price:
            new_order = convert_stoploss_to_order(sell_order)
            new_order.save()
            match_order(new_order)
            sell_order.delete()
    

# ============================================================
# BULK USER UPLOAD
# ============================================================
 
REQUIRED_HEADERS = ['Roll', 'Name', 'Mail', 'Role', 'Password']
VALID_ROLES = {'TRADER', 'MARKET_MAKER'}
 
 
def _validate_csv_row(row_num, roll, username, mail, role, password):
    """Returns a list of error strings. Empty list = valid row."""
    errors = []
 
    # Roll: numbers only
    if not roll:
        errors.append('Roll is empty.')
    elif not roll.isdigit():
        errors.append(f'Roll "{roll}" must contain numbers only.')
 
    # Username: alphabets only
    if not username:
        errors.append('Username is empty.')
    elif not username.isalpha():
        errors.append(f'Username "{username}" must contain alphabets only.')
 
    # Mail: must contain '@' and '.'
    if not mail:
        errors.append('Mail is empty.')
    elif '@' not in mail or '.' not in mail:
        errors.append(f'Mail "{mail}" is not a valid email address.')
 
    # Role
    if not role:
        errors.append('Role is empty.')
    elif role not in VALID_ROLES:
        errors.append(f'Role "{role}" must be exactly TRADER or MARKET_MAKER.')
 
    # Password: must contain letters, digits, and special characters
    if not password:
        errors.append('Password is empty.')
    else:
        has_alpha   = bool(re.search(r'[a-zA-Z]', password))
        has_digit   = bool(re.search(r'\d', password))
        has_special = bool(re.search(r'[^a-zA-Z0-9]', password))
        if not (has_alpha and has_digit and has_special):
            errors.append('Password must contain a mix of alphabets, numbers, and special characters.')
 
    return errors
 
 
@login_required
def bulk_user_upload(request):
    if not _is_admin(request.user):
        return redirect('role_router')
 
    results = None
 
    if request.method == 'POST':
        csv_file = request.FILES.get('csv_file')
 
        if not csv_file:
            messages.error(request, 'No file uploaded.')
            return render(request, 'trading/bulk_upload.html', {'results': results})
 
        if not csv_file.name.endswith('.csv'):
            messages.error(request, 'Please upload a valid .csv file.')
            return render(request, 'trading/bulk_upload.html', {'results': results})
 
        try:
            decoded = csv_file.read().decode('utf-8-sig')  # utf-8-sig strips BOM if present
        except UnicodeDecodeError:
            messages.error(request, 'File encoding error. Please save your CSV as UTF-8.')
            return render(request, 'trading/bulk_upload.html', {'results': results})
 
        reader = csv.DictReader(io.StringIO(decoded))
        print("FIELDNAMES:", reader.fieldnames)
        actual_headers = [h.strip() for h in reader.fieldnames if h.strip()] if reader.fieldnames else []
 
        # Validate headers exactly
        if not actual_headers or actual_headers != list(REQUIRED_HEADERS):
            messages.error(
                request,
                f'Invalid headers. Expected exactly: {", ".join(REQUIRED_HEADERS)}'
            )
            return render(request, 'trading/bulk_upload.html', {'results': results})
 
        created_users = []
        skipped_users = []
        invalid_rows  = []
 
        for row_num, row in enumerate(reader, start=2):  # row 1 is header
            roll     = (row.get('Roll')     or '').strip()
            name = (row.get('Name') or '').strip()
            mail     = (row.get('Mail')     or '').strip()
            role     = (row.get('Role')     or '').strip()
            password = (row.get('Password') or '').strip()
            print(f"ROW {row_num}: roll='{roll}' name='{name}' isdigit={roll.isdigit()}")
 
            # Field-level validation
            row_errors = _validate_csv_row(row_num, roll, name, mail, role, password)
            if row_errors:
                invalid_rows.append({'row': row_num, 'Name': name or '—', 'errors': row_errors})
                continue
 
            # Skip if Django auth user already exists
            if AuthUser.objects.filter(username=roll).exists():
                skipped_users.append({'row': row_num, 'Name': name, 'reason': f'Roll {roll} already exists.'})
                continue
 
            if BaseUser.objects.filter(username=roll).exists():
                skipped_users.append({'row': row_num, 'Name': name, 'reason': f'BaseUser Roll {roll} already exists.'})
                continue
 
            try:
                with transaction.atomic():
                    # 1. Create Django auth user (password is hashed automatically)
                    AuthUser.objects.create_user(
                        username=roll,         
                        first_name=name,  
                        email=mail,
                        password=password,
                    )
 
                    # # 2. Create BaseUser
                    # base_user, created = BaseUser.objects.get_or_create(
                    #     username=roll,       
                    #     defaults={'role': role}
                    # )
                    
                    # # If the signal created it without a role, update it now
                    # if not created and base_user.role != role:
                    #     base_user.role = role
                    #     base_user.save()
 
                    # 3. Create role-specific profile
                    if role == 'TRADER':
                        Trader.objects.get_or_create(username=roll, defaults={'role': 'TRADER'})
                    elif role == 'MARKET_MAKER':
                        MarketMaker.objects.get_or_create(username=roll, defaults={'role': 'MARKET_MAKER'})
 
                created_users.append({'row': row_num, 'username': name, 'role': role, 'mail': mail})
 
            except Exception as e:
                invalid_rows.append({'row': row_num, 'username': name, 'errors': [str(e)]})
 
        results = {
            'created':       created_users,
            'skipped':       skipped_users,
            'invalid':       invalid_rows,
            'total_created': len(created_users),
            'total_skipped': len(skipped_users),
            'total_invalid': len(invalid_rows),
        }
 
    return render(request, 'trading/bulk_upload.html', {'results': results})
 
 
# ============================================================
# BULK USER DELETE
# ============================================================
 
@login_required
def bulk_user_delete(request):

    import logging
    log = logging.getLogger(__name__)
    log.warning(f"METHOD={request.method} user={request.user} is_auth={request.user.is_authenticated} is_super={request.user.is_superuser}")
    
    if not _is_admin(request.user):
        return redirect('role_router')

    results = None

    if request.method == 'POST':
        csv_file = request.FILES.get('csv_file')

        if not csv_file:
            messages.error(request, 'No file uploaded.')
            return render(request, 'trading/bulk_delete.html', {'results': results})

        if not csv_file.name.endswith('.csv'):
            messages.error(request, 'Please upload a valid .csv file.')
            return render(request, 'trading/bulk_delete.html', {'results': results})

        try:
            decoded = csv_file.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            messages.error(request, 'File encoding error. Please save your CSV as UTF-8.')
            return render(request, 'trading/bulk_delete.html', {'results': results})

        reader = csv.DictReader(io.StringIO(decoded))

        DELETE_HEADERS = ['Roll', 'Name']
        if not reader.fieldnames or [h.strip() for h in reader.fieldnames[:2]] != DELETE_HEADERS:
            messages.error(
                request,
                f'Invalid headers. First two columns must be: {", ".join(DELETE_HEADERS)}'
            )
            return render(request, 'trading/bulk_delete.html', {'results': results})

        deleted_users = []
        not_found     = []
        error_rows    = []

        for row_num, row in enumerate(reader, start=2):
            roll = (row.get('Roll') or '').strip()
            name = (row.get('Name') or '').strip()
            print(f"ROW {row_num}: roll='{roll}' name='{name}' isdigit={roll.isdigit()}")
            display_name = name if name else roll

            if not roll:
                error_rows.append({'row': row_num, 'name': display_name or '—', 'reason': 'Roll number is empty.'})
                continue

            if not roll.isdigit():
                error_rows.append({'row': row_num, 'name': display_name, 'reason': f'Roll "{roll}" must be numbers only.'})
                continue

            try:
                with transaction.atomic():
                    auth_exists = AuthUser.objects.filter(username=roll).exists()
                    base_exists = BaseUser.objects.filter(username=roll).exists()

                    if not auth_exists and not base_exists:
                        not_found.append({'row': row_num, 'name': display_name})
                        continue

                    Trader.objects.filter(username=roll).delete()
                    MarketMaker.objects.filter(username=roll).delete()
                    BaseUser.objects.filter(username=roll).delete()
                    AuthUser.objects.filter(username=roll).delete()

                    deleted_users.append({'row': row_num, 'name': display_name})

            except Exception as e:
                error_rows.append({'row': row_num, 'name': display_name, 'reason': str(e)})

        results = {
            'deleted':         deleted_users,
            'not_found':       not_found,
            'errors':          error_rows,
            'total_deleted':   len(deleted_users),
            'total_not_found': len(not_found),
            'total_errors':    len(error_rows),
        }

    return render(request, 'trading/bulk_delete.html', {'results': results})