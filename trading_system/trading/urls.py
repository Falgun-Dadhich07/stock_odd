from django.urls import path
from . import views
from django.contrib.auth import views as auth_views


urlpatterns = [
    # path('', auth_views.LoginView.as_view(template_name='trading/login.html'), name='login'),
    # path('trader/', views.trader_home, name='trader_home'),
    # path('market-maker/', views.market_maker_home, name='market_maker_home'),
    # path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    # path('home/', views.home, name='home'),
    # path('orderbook/', views.orderbook, name='orderbook'),
    # path('modify/', views.modify, name='modify'),
    # path('clear/', views.clear_database, name='clear_database'),
    # path('orderbook/get_best_ask/', views.get_best_ask, name='get_best_ask'),
    # path('orderbook/get_best_bid/', views.get_best_bid, name='get_best_bid'),
    # path('orderbook/get_buy_orders/', views.get_buy_orders, name='get_buy_orders'),
    # path('orderbook/get_sell_orders/', views.get_sell_orders, name='get_sell_orders'),
    # path('orderbook/get_recent_trades/', views.get_recent_trades, name='get_recent_trades'),
    # path('orderbook/get_best_ask/', views.get_best_ask, name='get_best_ask'),
    # path('modify/get_best_bid/', views.get_best_bid, name='get_best_bid'),
    # path('modify/get_buy_orders/', views.get_buy_orders, name='get_buy_orders'),
    # path('modify/get_sell_orders/', views.get_sell_orders, name='get_sell_orders'),
    # path('modify_order/', views.modify_order_page, name='modify_order'),
    # path('modify_order/update_prev_order/', views.update_prev_order, name='update_prev_order'),
    # path('cancel_order/', views.cancel_order, name='cancel_order'),
    # path('cancel_stoploss_order/', views.cancel_stoploss_order, name='cancel_stoploss_order'),

    # Auth
    path('', auth_views.LoginView.as_view(template_name='trading/login.html', redirect_authenticated_user=True), name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('role_router/', views.role_router, name='role_router'),
    path('admin_home/', views.admin_home, name='admin_home'),

    # =========================
    # ADMIN - Bulk Operations
    # =========================
    path('bulk_user_upload/', views.bulk_user_upload, name='bulk_user_upload'),
    path('bulk-delete/', views.bulk_user_delete, name='bulk_user_delete'),

    # =========================
    # TRADER
    # =========================
    path('trader_home/', views.trader_home, name='trader_home'),
    path('trader/orderbook/', views.orderbook, name='trader_orderbook'),
    path('trader/cancel/', views.cancel_order, name='trader_cancel'),

    # =========================
    # MARKET MAKER
    # =========================
    path('market_maker_home/', views.market_maker_home, name='mm_home'),
    path('market-maker/orderbook/', views.orderbook, name='mm_orderbook'),
    path('orderbook/', views.orderbook, name='orderbook'),

    # reuse EXISTING modify functionality
    path('market-maker/modify/', views.modify, name='mm_modify'),
    path('market-maker/modify_order/', views.modify_order_page, name='mm_modify_order'),
    path('market-maker/modify_order/update_prev_order/', views.update_prev_order, name='mm_update_prev_order'),
    path('modify/', views.modify, name='modify'),
    path('modify_order/', views.modify_order_page, name='modify_page'),
    path('modify_order/update_prev_order/', views.update_prev_order, name='update_prev_order'),
    path('market-maker/modify_order/get_buy_orders/', views.get_buy_orders, name='get_buy_orders'),
    path('market-maker/modify_order/get_sell_orders/', views.get_sell_orders, name='get_sell_orders'),

    # Cancel
    path('market-maker/cancel/', views.cancel_order, name='mm_cancel'),
    path('market-maker/cancel-stoploss/', views.cancel_stoploss_order, name='mm_cancel_stoploss'),
    path('cancel_order/', views.cancel_order, name='cancel_order'),
    path('cancel_stoploss_order/', views.cancel_stoploss_order, name='cancel_stoploss_order'),

    # Shared utilities
    path('clear/', views.clear_database, name='clear_database'),

    path('orderbook/get_best_ask/', views.get_best_ask, name='get_best_ask'),
    path('orderbook/get_best_bid/', views.get_best_bid, name='get_best_bid'),
    path('orderbook/get_buy_orders/', views.get_buy_orders, name='get_buy_orders'),
    path('orderbook/get_sell_orders/', views.get_sell_orders, name='get_sell_orders'),
    path('orderbook/get_recent_trades/', views.get_recent_trades, name='get_recent_trades'),
    path('orderbook/get_market_status/', views.get_market_status, name='get_market_status'),
    path('market/toggle_market_pause/', views.toggle_market_pause, name='toggle_market_pause'),
]

