from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User

class ProcessedInvoice(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="invoices", null=True, blank=True)
    
    # Original image
    image = models.ImageField(upload_to="invoices/", null=True, blank=True)
    
    # Store complete extracted data as JSON
    extracted_data = models.JSONField(default=dict, help_text="Complete extracted data from AI")
    
    # Quick lookup fields (denormalized for performance)
    vendor_name = models.CharField(max_length=255, blank=True, db_index=True)
    invoice_number = models.CharField(max_length=100, blank=True, db_index=True)
    invoice_date = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    amount_due = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    payment_status = models.CharField(max_length=50, default='Not Paid', blank=True, null=True)
    language_detected = models.CharField(max_length=10, blank=True)
    
    # Structured outputs
    xml_output = models.TextField(blank=True)
    csv_output = models.TextField(blank=True)
    
    # Metadata
    created_at = models.DateTimeField(default=timezone.now)
    raw_ai_response = models.TextField(blank=True, help_text="Full raw text from Gemini")

    def __str__(self):
        return f"{self.vendor_name} - {self.invoice_number}"