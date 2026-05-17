from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager # Switched to BaseUserManager
from django.utils.timezone import now
from django.core.exceptions import ValidationError

class CustomUserManager(BaseUserManager):
    def create_user(self, user_id, email=None, password=None, **extra_fields):
        if not user_id:
            raise ValueError("The User ID field must be set")
        email = self.normalize_email(email)
        # username must be set (AbstractUser requires it) — mirror user_id
        extra_fields.setdefault('username', user_id)
        user = self.model(user_id=user_id, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, user_id, email=None, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', 'ADMIN')
        extra_fields.setdefault('name', 'Admin User')
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        return self.create_user(user_id, email, password, **extra_fields)


class BaseUser(AbstractUser):
    user_id = models.CharField(max_length=100, unique=True, verbose_name="User ID")
    name = models.CharField(max_length=150)

    ROLE_CHOICES = (
        ('TRADER', 'Trader'),
        ('MARKET_MAKER', 'Market Maker'),
        ('ADMIN', 'Admin'),
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)

    # Use user_id for login instead of username.
    # username still exists (AbstractUser) but we mirror it from user_id
    # in the manager so it satisfies the unique constraint silently.
    USERNAME_FIELD = 'user_id'
    REQUIRED_FIELDS = ['name', 'email']

    objects = CustomUserManager()

    groups = models.ManyToManyField(
        'auth.Group',
        related_name='base_user_groups',
        blank=True,
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        related_name='base_user_permissions',
        blank=True,
    )

    def __str__(self):
        return f"{self.name} ({self.user_id})"


# Trader and MarketMaker are MTI children of BaseUser.
# They add NO extra DB fields — only the Python helper method.
# Do NOT create their rows explicitly in upload/register views;
# the role field on BaseUser is the source of truth for routing.
class Trader(BaseUser):
    class Meta:
        proxy = True  # <-- KEY CHANGE: proxy instead of MTI

    def allowed_order_modes(self):
        return ['MARKET']


class MarketMaker(BaseUser):
    class Meta:
        proxy = True  # <-- KEY CHANGE: proxy instead of MTI

    def allowed_order_modes(self):
        return ['LIMIT']

from datetime import datetime

class Order(models.Model):
    ORDER_TYPE_CHOICES = [
        ('BUY', 'Buy'),
        ('SELL', 'Sell'),
    ]

    ORDER_MODE_CHOICES = [
        ('LIMIT', 'Limit'),
        ('MARKET', 'Market'),
    ]

    ROLE_CHOICES = [
        ('TRADER', 'Trader'),
        ('MARKET_MAKER', 'Market Maker'),
    ]

    def clean(self):
        if self.user_role == 'TRADER' and self.order_mode != 'MARKET':
            raise ValidationError("Trader can only place MARKET orders")

        if self.user_role == 'MARKET_MAKER' and self.order_mode != 'LIMIT':
            raise ValidationError("Market Maker can only place LIMIT orders")

        if self.order_mode == 'MARKET' and self.price is not None:
            raise ValidationError("Market orders cannot have price")

        if self.order_mode == 'LIMIT' and self.price is None:
            raise ValidationError("Limit orders must have price")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


    user = models.ForeignKey(BaseUser, on_delete=models.CASCADE)
    user_role = models.CharField(max_length=30, choices=ROLE_CHOICES)
    order_type = models.CharField(max_length=10, choices=ORDER_TYPE_CHOICES)
    order_mode = models.CharField(max_length=10, choices=ORDER_MODE_CHOICES)
    quantity = models.IntegerField()
    disclosed = models.IntegerField(default=0)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    is_matched = models.BooleanField(default=False)
    # original_quantity = models.IntegerField()
    original_quantity = models.IntegerField(default=0)  # New field added

 

    is_ioc = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.order_type} {self.order_mode} Order #{self.id} by {self.user}"

class Trade(models.Model):
    buyer = models.ForeignKey(BaseUser, related_name='buy_trades', on_delete=models.CASCADE)
    seller = models.ForeignKey(BaseUser, related_name='sell_trades', on_delete=models.CASCADE)
    quantity = models.IntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Trade #{self.id}: {self.buyer} ⇄ {self.seller} ({self.quantity} @ {self.price})"


class Stoploss_Order(models.Model):
    ORDER_TYPE_CHOICES = [
        ('BUY', 'Buy'),
        ('SELL', 'Sell'),
    ]
    ORDER_MODE_CHOICES = [
        ('LIMIT', 'Limit'),
        ('MARKET', 'Market'),
    ]

    user = models.ForeignKey(BaseUser, on_delete=models.CASCADE)
    user_role = models.CharField(max_length=30, choices=BaseUser.ROLE_CHOICES, default='MARKET_MAKER')
    order_type = models.CharField(max_length=10, choices=ORDER_TYPE_CHOICES)
    order_mode = models.CharField(max_length=10, choices=ORDER_MODE_CHOICES, default='MARKET')
    quantity = models.IntegerField()
    disclosed = models.IntegerField(default=0)
    target_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    is_matched = models.BooleanField(default=False)
    is_ioc = models.BooleanField(default=False)

    def __str__(self):
        return f"StopLoss {self.order_type} Order #{self.id} (Target: {self.target_price})"


class MarketControl(models.Model):
    """Simple singleton model to control market state (paused/unpaused)."""
    paused = models.BooleanField(default=False)
    message = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return f"MarketControl(paused={self.paused})"
