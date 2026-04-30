from django.urls import path
from .views import (
    DashboardView, 
    ProcessInvoiceView, 
    InvoiceDetailView, 
    InvoicesListView,
    InternationalInvoicesView,
    TransformInvoiceView,
    ExportTransformedInvoiceView,
    CustomLoginView,
    CustomLogoutView,
    RegisterView
)

urlpatterns = [
    path('login/', CustomLoginView.as_view(), name='login'),
    path('register/', RegisterView.as_view(), name='register'),
    path('logout/', CustomLogoutView.as_view(), name='logout'),
    path('', DashboardView.as_view(), name='dashboard'),
    path('invoices/', InvoicesListView.as_view(), name='invoices'),
    path('international/', InternationalInvoicesView.as_view(), name='international'),
    path('transform-invoice/', TransformInvoiceView.as_view(), name='transform_invoice'),
    path('export-pdf/', ExportTransformedInvoiceView.as_view(), name='export_pdf'),
    path('process-invoice/', ProcessInvoiceView.as_view(), name='process_invoice'),
    path('get-invoice/<int:pk>/', InvoiceDetailView.as_view(), name='invoice_detail'),
]