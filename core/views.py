from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework import status
from django.shortcuts import render, redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from .models import ProcessedInvoice
from .service_layer import get_invoice_extractor, dict_to_eu_einvoice_xml, dict_to_csv
import json

from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login

class CustomLoginView(LoginView):
    template_name = "login.html"
    redirect_authenticated_user = True

class RegisterView(APIView):
    def get(self, request):
        if request.user.is_authenticated:
            return redirect('dashboard')
        form = UserCreationForm()
        return render(request, "register.html", {"form": form})
    
    def post(self, request):
        print("RegisterView	")
        form = UserCreationForm(request.POST)
        allowed_users = ["mik3", "majk"]
        username = request.POST.get('username')
        print(f"Username: {username} is in {allowed_users}")
        if form.is_valid() and username in allowed_users:
            print("allowed")
            user = form.save()
            login(request, user)
            return redirect('dashboard')
        print("nope!")
        return render(request, "register.html", {"form": form})

class CustomLogoutView(LogoutView):
    next_page = 'login'

class DashboardView(LoginRequiredMixin, APIView):
    """Serve the modern dashboard UI."""
    def get(self, request):
        invoices = ProcessedInvoice.objects.filter(user=request.user).order_by('-created_at')[:10]
        return render(request, "dashboard.html", {"invoices": invoices})

class InvoicesListView(LoginRequiredMixin, APIView):
    """Serve the 'All Invoices' page with filters."""
    def get(self, request):
        user=request.user
        if user.is_superuser:
            invoices = ProcessedInvoice.objects.all().order_by('-created_at')
        else:
            invoices = ProcessedInvoice.objects.filter(user=user).order_by('-created_at')
        return render(request, "invoices.html", {"invoices": invoices})

class InternationalInvoicesView(LoginRequiredMixin, APIView):
    """View to select and transform invoices internationally."""
    def get(self, request):
        invoices = ProcessedInvoice.objects.filter(user=request.user).order_by('-created_at')
        return render(request, "international.html", {"invoices": invoices})

class TransformInvoiceView(LoginRequiredMixin, APIView):
    """API endpoint to transform an invoice to another country format."""
    print("TransformInvoiceView i am here")
    def post(self, request):
        invoice_id = request.data.get('invoice_id')
        print("invoice_id", invoice_id)
        target_country = request.data.get('target_country')
        print("target_country", target_country)
        target_lang = request.data.get('target_language', 'English')
        print("target_lang", target_lang)
        
        try:
            invoice = ProcessedInvoice.objects.get(id=invoice_id, user=request.user)
            print("invoice", invoice)
            extractor = get_invoice_extractor()
            result = extractor.transform_invoice(invoice.extracted_data, target_country, target_lang)
            return Response(result)
        except ProcessedInvoice.DoesNotExist:
            return Response({"error": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

from django.http import HttpResponse
from .service_layer import generate_invoice_pdf

class ExportTransformedInvoiceView(LoginRequiredMixin, APIView):
    """Generate a PDF from transformed invoice data."""
    def post(self, request):
        data = request.data.get('transformed_data')
        if not data:
            return Response({"error": "No data provided"}, status=400)
        
        pdf_buffer = generate_invoice_pdf(data)
        response = HttpResponse(pdf_buffer, content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="transformed_invoice.pdf"'
        return response


class ProcessInvoiceView(LoginRequiredMixin, APIView):
    print("ProcessInvoiceView i am here")
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        invoice_file = request.FILES.get("image") or request.FILES.get("file")
        if not invoice_file:
            return Response({"error": "No invoice file provided"}, status=status.HTTP_400_BAD_REQUEST)
        
        content_type = invoice_file.content_type
        file_bytes = invoice_file.read()
        
        try:
            print("try extract i am here")
            extractor = get_invoice_extractor()
            extracted_data = extractor.extract_from_file(file_bytes, content_type)
        except Exception as e:
            return Response({"error": f"AI extraction failed: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        try:
            xml_output = dict_to_eu_einvoice_xml(extracted_data)
            csv_output = dict_to_csv(extracted_data)
        except Exception as e:
            xml_output = f"<!-- Conversion error: {e} -->"
            csv_output = f"# Conversion error: {e}"
        
        invoice_record = ProcessedInvoice(
            user=request.user,
            image=invoice_file if content_type.startswith("image/") else None,
            extracted_data=extracted_data,
            vendor_name=extracted_data.get('vendor', {}).get('name', ''),
            invoice_number=extracted_data.get('invoice', {}).get('number', ''),
            invoice_date=extracted_data.get('invoice', {}).get('date') or None,
            currency=extracted_data.get('invoice', {}).get('currency', ''),
            amount_due=extracted_data.get('financials', {}).get('amount_due'),
            payment_status=extracted_data.get('invoice', {}).get('payment_status') or 'Not Paid',
            language_detected=extracted_data.get('language', ''),
            xml_output=xml_output,
            csv_output=csv_output,
            raw_ai_response=json.dumps(extracted_data, indent=2)
        )
        invoice_record.save()
        
        return Response({
            "id": invoice_record.id,
            "filename": invoice_file.name,
            "extracted_data": extracted_data,
            "xml": xml_output,
            "csv": csv_output,
            "created_at": invoice_record.created_at.isoformat(),
        })

class InvoiceDetailView(LoginRequiredMixin, APIView):
    def get(self, request, pk):
        user = request.user
        if user.is_superuser:
            try:
                invoice = ProcessedInvoice.objects.get(pk=pk)
            except ProcessedInvoice.DoesNotExist:
                return Response({"error": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)
        else:
            try:
                invoice = ProcessedInvoice.objects.get(pk=pk, user=user)
            except ProcessedInvoice.DoesNotExist:
                return Response({"error": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)
        
        return Response({
                "id": invoice.id,
                "extracted_data": invoice.extracted_data,
                "xml": invoice.xml_output,
                "csv": invoice.csv_output,
                "image_url": invoice.image.url if invoice.image else None,
                "created_at": invoice.created_at.isoformat(),
            })
    