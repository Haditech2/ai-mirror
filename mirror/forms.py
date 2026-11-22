from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.validators import MaxValueValidator, MinValueValidator

from .models import EventSettings, Gown

INPUT_CLASS = (
    "mt-2 h-12 w-full rounded-xl border border-white/10 bg-black/20 px-4 "
    "text-sm text-white outline-none transition focus:border-champagne-300/50"
)
TEXTAREA_CLASS = INPUT_CLASS.replace("h-12", "h-auto py-3")


class ConsoleAuthenticationForm(AuthenticationForm):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "autofocus": True,
                "autocomplete": "username",
                "placeholder": "Administrator username",
                "class": "min-w-0 flex-1 bg-transparent text-sm text-white outline-none placeholder:text-white/20",
            }
        ),
    )
    password = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "placeholder": "Your secure password",
                "class": "min-w-0 flex-1 bg-transparent text-sm text-white outline-none placeholder:text-white/20",
            }
        ),
    )

    def confirm_login_allowed(self, user) -> None:
        super().confirm_login_allowed(user)
        if not user.is_staff:
            raise forms.ValidationError(
                "This account does not have event-console access.",
                code="not_staff",
            )


class ConsentForm(forms.Form):
    consent = forms.BooleanField(
        error_messages={"required": "Consent is required before a photograph can be captured."}
    )


class CaptureForm(forms.Form):
    photo = forms.FileField()


class GownSelectionForm(forms.Form):
    gown = forms.ModelChoiceField(
        queryset=Gown.objects.none(),
        empty_label=None,
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["gown"].queryset = Gown.objects.filter(is_enabled=True, is_archived=False)


class GownForm(forms.ModelForm):
    image = forms.FileField(required=False)
    cap = forms.FileField(required=False)
    remove_cap = forms.BooleanField(required=False)

    class Meta:
        model = Gown
        fields = ("name", "color", "description", "display_order", "is_enabled")
        widgets = {
            "name": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": "e.g. Chancellor's Ceremonial Gown"}
            ),
            "color": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Burgundy or #6f1931"}),
            "description": forms.Textarea(
                attrs={
                    "class": TEXTAREA_CLASS,
                    "rows": 4,
                    "placeholder": "Describe the garment, its trim and ceremonial character.",
                }
            ),
            "display_order": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "max": 999}),
            "is_enabled": forms.CheckboxInput(attrs={"class": "h-5 w-5 accent-[#e4cb8d]"}),
        }

    def clean(self):
        cleaned = super().clean()
        if not self.instance.pk and not cleaned.get("image"):
            self.add_error("image", "Choose a PNG, JPEG or WebP garment reference image.")
        return cleaned


class EventSettingsForm(forms.ModelForm):
    ai_api_key = forms.CharField(
        required=False,
        max_length=500,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={"class": INPUT_CLASS, "autocomplete": "new-password"},
        ),
    )
    logo = forms.FileField(required=False)
    background = forms.FileField(required=False)
    inactivity_timeout_seconds = forms.IntegerField(
        validators=[MinValueValidator(45), MaxValueValidator(900)],
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 45, "max": 900}),
    )
    retention_hours = forms.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(8760)],
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1, "max": 8760}),
    )

    class Meta:
        model = EventSettings
        fields = (
            "professor_name",
            "event_title",
            "event_date",
            "welcome_headline",
            "welcome_text",
            "privacy_notice",
            "inactivity_timeout_seconds",
            "retention_hours",
            "ai_provider",
            "replicate_model_owner",
            "replicate_model_name",
            "replicate_model_version",
            "fashn_model_name",
        )
        widgets = {
            "professor_name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "event_title": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "event_date": forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}),
            "welcome_headline": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "welcome_text": forms.Textarea(attrs={"class": TEXTAREA_CLASS, "rows": 3}),
            "privacy_notice": forms.Textarea(attrs={"class": TEXTAREA_CLASS, "rows": 5}),
            "ai_provider": forms.Select(
                choices=(
                    ("disabled", "Disabled"),
                    ("replicate", "Replicate / IDM-VTON"),
                    ("fashn", "FASHN virtual try-on"),
                    ("openai", "OpenAI GPT Image"),
                ),
                attrs={"class": INPUT_CLASS},
            ),
            "replicate_model_owner": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "replicate_model_name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "replicate_model_version": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "fashn_model_name": forms.TextInput(attrs={"class": INPUT_CLASS}),
        }
