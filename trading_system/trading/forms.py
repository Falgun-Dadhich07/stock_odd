from django import forms
from django.contrib.auth.forms import UserCreationForm
from trading.models import BaseUser


class UserRegisterForm(UserCreationForm):
    user_id = forms.CharField(max_length=100, required=True, label="User ID")
    name = forms.CharField(max_length=150, required=True, label="Name")
    email = forms.EmailField(required=True)
    role = forms.ChoiceField(
        choices=(
            ('TRADER', 'Trader'),
            ('MARKET_MAKER', 'Market Maker'),
        ),
        required=True
    )

    class Meta(UserCreationForm.Meta):
        model = BaseUser
        fields = ['user_id', 'name', 'email', 'role']

    def save(self, commit=True):
        user = super().save(commit=False)
        user.user_id = self.cleaned_data.get('user_id')
        user.username = self.cleaned_data.get('user_id')
        user.name = self.cleaned_data.get('name')
        user.email = self.cleaned_data.get('email')
        user.role = self.cleaned_data.get('role')
        if commit:
            user.save()
        return user


class CSVUploadForm(forms.Form):
    csv_file = forms.FileField()


class UserDeleteCSVForm(forms.Form):
    csv_file = forms.FileField()