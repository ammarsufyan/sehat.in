from django import forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
import functools
import re
from difflib import SequenceMatcher
from django.conf import settings
from django.core.exceptions import (
    FieldDoesNotExist, ImproperlyConfigured, ValidationError,
)
from django.utils.functional import lazy
from django.utils.html import format_html, format_html_join
from django.utils.module_loading import import_string
from django.utils.translation import gettext as __, ngettext

UserModel = get_user_model()

@functools.lru_cache(maxsize=None)
def get_default_password_validators():
    return get_password_validators(settings.AUTH_PASSWORD_VALIDATORS)


def get_password_validators(validator_config):
    validators = []
    for validator in validator_config:
        try:
            klass = import_string(validator['NAME'])
        except ImportError:
            msg = "The module in NAME could not be imported: %s. Check your AUTH_PASSWORD_VALIDATORS setting."
            raise ImproperlyConfigured(msg % validator['NAME'])
        validators.append(klass(**validator.get('OPTIONS', {})))

    return validators


def validate_password(password, user=None, password_validators=None):
    """
    Validate whether the password meets all validator requirements.

    If the password is valid, return ``None``.
    If the password is invalid, raise ValidationError with all error messages.
    """
    errors = []
    if password_validators is None:
        password_validators = get_default_password_validators()
    for validator in password_validators:
        try:
            validator.validate(password, user)
        except ValidationError as error:
            errors.append(error)
    if errors:
        raise ValidationError(errors)


def password_changed(password, user=None, password_validators=None):
    """
    Inform all validators that have implemented a password_changed() method
    that the password has been changed.
    """
    if password_validators is None:
        password_validators = get_default_password_validators()
    for validator in password_validators:
        password_changed = getattr(validator, 'password_changed', lambda *a: None)
        password_changed(password, user)


def password_validators_help_texts(password_validators=None):
    """
    Return a list of all help texts of all configured validators.
    """
    help_texts = []
    if password_validators is None:
        password_validators = get_default_password_validators()
    for validator in password_validators:
        help_texts.append(validator.get_help_text())
    return help_texts

def _custom_password_validators_help_text_html(password_validators=None):
    """
    Return an HTML string with all help texts of all configured validators
    in an <ul>.
    """
    help_texts = password_validators_help_texts(password_validators)
    help_items = [format_html('<li>{}</li>', help_text) for help_text in help_texts]
    #<------------- append your hint here in help_items  ------------->
    return '<ul>%s</ul>' % ''.join(help_items) if help_items else ''

custom_password_validators_help_text_html = custom_validators_help_text_html=lazy(_custom_password_validators_help_text_html, str)

class CustomPasswordValidator:
    """
    Validate wheter the password is not macth all password validator
    """
    def __init__(self, min_length=8):
        self.min_length = min_length

    def validate(self, password, user=None):
        special_characters = "[~\!@#\$%\^&\*\(\)_\+{}\":;'\[\]]"
        lowercase = re.compile('[a-z]')
        uppercase = re.compile('[A-Z]')
        if not any(char.isdigit() for char in password):
            raise ValidationError(_('Kata sandi harus memiliki minimal 1 angka.'))
        if not any(char in special_characters for char in password):
            raise ValidationError(_('Kata sandi harus memiliki minimal 1 karakter spesial.'))
        if not lowercase.search(password) or not uppercase.search(password):
            raise ValidationError(_('Kata sandi harus mengandung satu huruf kecil dan satu huruf kapital!'))
        if len(password) < 8 or len(password) > 50:
            raise ValidationError(_('Kata sandi harus memiliki minimal %(min_length)d dan maksimal 50 karakter.') % {'min_length': self.min_length})

    def get_help_text(self):
        return "Kata sandi harus memiliki angka, huruf (Minimal 8 dan Maksimal 50), dan spesial karakter"

class UserAttributeSimilarityValidator:
    """
    Validate whether the password is sufficiently different from the user's
    attributes.

    If no specific attributes are provided, look at a sensible list of
    defaults. Attributes that don't exist are ignored. Comparison is made to
    not only the full attribute value, but also its components, so that, for
    example, a password is validated against either part of an email address,
    as well as the full address.
    """
    DEFAULT_USER_ATTRIBUTES = ('username', 'first_name', 'last_name', 'email')

    def __init__(self, user_attributes=DEFAULT_USER_ATTRIBUTES, max_similarity=0.7):
        self.user_attributes = user_attributes
        self.max_similarity = max_similarity

    def validate(self, password, user=None):
        if not user:
            return

        for attribute_name in self.user_attributes:
            value = getattr(user, attribute_name, None)
            if not value or not isinstance(value, str):
                continue
            value_parts = re.split(r'\W+', value) + [value]
            for value_part in value_parts:
                if SequenceMatcher(a=password.lower(), b=value_part.lower()).quick_ratio() >= self.max_similarity:
                    try:
                        verbose_name = str(user._meta.get_field(attribute_name).verbose_name)
                    except FieldDoesNotExist:
                        verbose_name = attribute_name
                    raise ValidationError(
                        _("Kata sandi Anda mirip dengan %(verbose_name)s."),
                        code='password_too_similar',
                        params={'verbose_name': verbose_name},
                    )

    def get_help_text(self):
        return _('Kata sandi Anda tidak boleh mirip dengan informasi personal Anda.')

class MyPasswordForm(forms.Form):
    """
    A form that lets a user change set their password without entering the old
    password
    """
    error_messages = {
        'password_mismatch': _('Kata sandi tidak cocok.'),
    }
    new_password1 = forms.CharField(
        label=_("Kata Sandi Baru"),
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        strip=False,
        help_text=custom_validators_help_text_html(),
    )
    new_password2 = forms.CharField(
        label=_("Konfirmasi Kata Sandi Baru"),
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_new_password2(self):
        password1 = self.cleaned_data.get('new_password1')
        password2 = self.cleaned_data.get('new_password2')
        if password1 and password2:
            if password1 != password2:
                raise ValidationError(
                    self.error_messages['password_mismatch'],
                    code='password_mismatch',
                )
        validate_password(password2, self.user)
        return password2

    def save(self, commit=True):
        password = self.cleaned_data["new_password1"]
        self.user.set_password(password)
        if commit:
            self.user.save()
        return self.user