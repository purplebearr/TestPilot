from django import forms

from .models import OrchestrationRun


class OrchestrationRunForm(forms.ModelForm):
    class Meta:
        model = OrchestrationRun
        fields = ["target_url", "user_inquiry"]

        widgets = {
            "target_url": forms.URLInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "https://example.com",
                }
            ),
            "user_inquiry": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "placeholder": "What would you like to test?",
                    "rows": 5,
                }
            ),
        }