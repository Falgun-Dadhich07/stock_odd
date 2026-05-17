from django.shortcuts import render
from django.core.mail import send_mail
# from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages
from django.shortcuts import redirect
from django.conf import settings
import logging
from django.contrib.auth.decorators import login_required
from trading.models import BaseUser

logger = logging.getLogger(__name__)
from .forms import UserRegisterForm
# Create your views here.
def register(request):
    form = UserRegisterForm()
    if request.method == 'POST':
        form = UserRegisterForm(request.POST)
        if form.is_valid():
            form.save()  
            username=form.cleaned_data.get('username')
            # messages.success(request, f'Account created for {username}!')/
            return redirect("login")

    else:
        form = UserRegisterForm()
    return render(request, 'trading/register.html', {'form': form})

import csv
from django.contrib.auth.models import User
from .forms import CSVUploadForm


def _is_admin_user(auth_user):
    return auth_user.is_superuser


@login_required
def bulk_user_upload(request):
    if not _is_admin_user(request.user):
        messages.error(request, 'Admin access required.')
        return redirect('role_router')

    success_count = 0
    error_count = 0

    if request.method == 'POST':
        form = CSVUploadForm(request.POST, request.FILES)
        if form.is_valid():
            csv_file = request.FILES['csv_file']
            decoded_file = csv_file.read().decode('utf-8').splitlines()
            csv_reader = csv.reader(decoded_file)
            next(csv_reader, None)

            for row in csv_reader:
                if len(row) >= 2:  # Ensure at least two columns exist
                    username, email = row[:2]  # Get username and email
                    password = row[2] if len(row) > 2 else 'defaultpassword'  # Set password if given, else default
                    
                    if not User.objects.filter(username=username).exists():
                        User.objects.create_user(username=username, email=email, password=password)
                    try:
                        send_email_to_user(username, password, email)
                        success_count += 1
                    
                    except Exception as e:
                        logger.error(f"Error processing row: {row}. Error: {str(e)}")
                        error_count += 1
            
            messages.success(request, f"Users created successfully! and email sent to {success_count} users. and error in {error_count} users")
            return redirect('admin_home')

    else:
        form = CSVUploadForm()

    return render(request, 'trading/bulk_upload.html', {'form': form})

def send_email_to_user(username, password, email):
    """
    Send email with credentials to the user
    """
    subject = 'Your OrderBook Account Details'
    message = f"""
    Hello {username},
    
    Your account has been created successfully.
    
    Your login credentials are:
    Username: {username}
    Password: {password}
    
    Please change your password after your first login for security reasons.
    
    Regards,
    <FACulty Name>
    """
    
    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [email],
            fail_silently=False,
        )
        logger.info(f"Email sent successfully to {email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {email}. Error: {str(e)}")
        return False


def send_deletion_email_to_user(username, email):
    """
    Send email notifying the user that their account has been deleted
    """
    subject = 'Your OrderBook Account Has Been Deleted'
    message = f"""
    Hello {username},

    We are writing to inform you that your OrderBook account has been deleted.

    If you believe this was done in error or have any questions, please contact
    your administrator.

    Regards,
    <FACulty Name>
    """

    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [email],
            fail_silently=False,
        )
        logger.info(f"Deletion email sent successfully to {email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send deletion email to {email}. Error: {str(e)}")
        return False





import csv
from django.contrib.auth.models import User
from django.shortcuts import render, redirect
from .forms import UserDeleteCSVForm
from django.contrib import messages

@login_required
def bulk_user_delete(request):
    if not _is_admin_user(request.user):
        messages.error(request, 'Admin access required.')
        return redirect('role_router')

    if request.method == 'POST':
        form = UserDeleteCSVForm(request.POST, request.FILES)
        if form.is_valid():
            csv_file = request.FILES['csv_file']
            decoded_file = csv_file.read().decode('utf-8').splitlines()
            csv_reader = csv.reader(decoded_file)
            
            deleted_count = 0
            not_found_users = []
            next(csv_reader, None)
            for row in csv_reader:
                if len(row) >= 1:
                    username = row[0].strip()
                    try:
                        user = User.objects.get(username=username)
                        user_email = user.email
                        user.delete()
                        deleted_count += 1
                        try:
                            send_deletion_email_to_user(username, user_email)
                        except Exception as email_error:
                            logger.error(f"Failed to send deletion email to {username}. Error: {str(email_error)}")
                    except User.DoesNotExist:
                        not_found_users.append(username)
            
            if deleted_count > 0:
                messages.success(request, f"{deleted_count} users deleted successfully.")
            if not_found_users:
                messages.warning(request, f"Users not found: {', '.join(not_found_users)}")

            return redirect('admin_home')

    else:
        form = UserDeleteCSVForm()

    return render(request, 'trading/bulk_delete.html', {'form': form})


from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import render, redirect
from django.contrib import messages

@login_required
def change_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            # Important to keep the user logged in after password change
            update_session_auth_hash(request, user)
            messages.success(request, 'Your password was successfully updated!')
            return redirect('role_router')
        else:
            messages.error(request, 'Your password was not updated! Please correct the error below.')
    else:
        form = PasswordChangeForm(request.user)
    try:
        base_user = BaseUser.objects.get(username=request.user.username)
        base_role = base_user.role
    except BaseUser.DoesNotExist:
        base_role = None
    return render(request, 'trading/reset_password.html', {
        'form': form,
        'base_role': base_role,
    })