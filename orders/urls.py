# orders/urls.py
from django.urls import path
from . import views
from django.contrib.auth.views import LogoutView

urlpatterns = [
    # ==================== KIRISH CHIQISH ====================
    path('login/', views.CustomLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(next_page='/login/'), name='logout'),
    
    # ==================== API ENDPOINTLAR ====================
    path('api/statistics/', views.api_statistics, name='api_statistics'),
    path('api/categories/', views.api_categories, name='api_categories'),
    path('api/materials/', views.api_materials, name='api_materials'),
    path('api/material/add/', views.api_material_add, name='api_material_add'),
    path('api/material/<int:material_id>/get/', views.api_material_get, name='api_material_get'),
    path('api/material/<int:material_id>/edit/', views.api_material_edit, name='api_material_edit'),
    path('api/material/<int:material_id>/delete/', views.api_material_delete, name='api_material_delete'),
    path('api/calculate/', views.api_calculate, name='api_calculate'),
    path('api/generate-svg/', views.api_generate_svg, name='api_generate_svg'),
    path('api/project/<int:pk>/', views.api_project_detail, name='api_project_detail'),
    path('api/find-material/', views.find_material_by_code_api, name='find_material_api'),
    path('api/save-scanned-transactions/', views.save_scanned_transactions_api, name='save_scanned_transactions_api'),
    
    # ==================== QARZDORLAR (DEBTS) API ====================
    path('cash/api/debts/', views.cash_api_debts, name='cash_api_debts'),
    path('cash/api/debt/create/', views.cash_api_debt_create, name='cash_api_debt_create'),
    path('cash/api/debt/<str:debt_id>/json/', views.cash_api_debt_json, name='cash_api_debt_json'),
    path('cash/api/debt/<str:debt_id>/edit/', views.cash_api_debt_edit, name='cash_api_debt_edit'),
    path('cash/api/debt/<str:debt_id>/delete/', views.cash_api_debt_delete, name='cash_api_debt_delete'),
    path('cash/api/debt/payment/', views.cash_api_debt_payment, name='cash_api_debt_payment'),
    path('cash/api/debt-transactions/', views.cash_api_debt_transactions, name='cash_api_debt_transactions'),
    path('cash/api/debt-transaction/<str:transaction_id>/json/', views.cash_api_debt_transaction_json, name='cash_api_debt_transaction_json'),
    path('cash/api/debtors-list/', views.cash_api_debtors_list, name='cash_api_debtors_list'),
    
    # ==================== ASOSIY BOSHQARUV ====================
    path('', views.order_list, name='order_list'),
    path('warehouse/', views.warehouse_dashboard, name='warehouse_dashboard'),
    path('warehouse/add/', views.add_material, name='add_material'),
    path('material/<int:material_id>/edit/', views.edit_material, name='edit_material'),
    path('material/<int:material_id>/delete/', views.delete_material, name='delete_material'),
    path('material/output/', views.material_output, name='material_output'),
    path('outputs/history/', views.output_history, name='output_history'),
    path('outputs/export/excel/', views.export_outputs_excel, name='export_outputs_excel'),
    path('inventory/export/excel/', views.export_inventory_excel, name='export_inventory_excel'),
    path('inventory/list/', views.material_list, name='material_list'),
    path('inventory/transaction/create/', views.material_transaction_create, name='material_transaction_create'),
    path('fast-scanner/', views.fast_scanner_view, name='fast_scanner'),
    path('import-excel/', views.import_excel_api, name='import_excel_api'),
    path('transactions/add/', views.add_transaction_view, name='add_transaction'),
    path('transactions/remove/', views.remove_transaction_view, name='remove_transaction'),
    
    # ==================== BUYURTMA OPERATSIYALARI ====================
    path('create/', views.order_create, name='order_create'),
    path('edit/<int:pk>/', views.order_edit, name='order_edit'),
    path('delete/<int:pk>/', views.order_delete, name='order_delete'),
    path('detail/<int:pk>/', views.order_detail, name='order_detail'),
    path('upload-order-image/', views.upload_order_image, name='upload_order_image'),
    path('archive/', views.order_archive, name='order_archive'),
    
    # ==================== BUYURTMA BOSQICHLARI ====================
    path('confirm/<int:pk>/', views.order_confirm, name='order_confirm'),
    path('reject/<int:pk>/', views.order_reject, name='order_reject'),
    path('start/<int:pk>/', views.order_start_production, name='order_start_production'),
    path('finish/<int:pk>/', views.order_finish, name='order_finish'),
    path('complete/<int:pk>/', views.order_complete, name='order_complete'),
    
    # ==================== USTALAR ====================
    path('order/<int:pk>/worker-accept/', views.order_worker_accept, name='order_worker_accept'),
    path('order/<int:pk>/worker-start/', views.order_worker_start, name='order_worker_start'),
    path('order/<int:pk>/worker-finish/', views.order_worker_finish, name='order_worker_finish'),
    path('worker-panel/', views.worker_panel, name='worker_panel'),
    path('worker-orders/<int:worker_id>/', views.worker_orders, name='worker_orders'),
    path('worker-my-orders/', views.worker_panel, name='worker_my_orders'),
    path('worker-report/', views.worker_activity_report_view, name='worker_activity_report'),
    path('worker-report/export-csv/', views.export_worker_activity_csv, name='export_worker_activity_csv'),
    path('rankings/', views.rankings_view, name='ranking'),
    
    # ==================== HISOBOTLAR ====================
    path('report/weekly/', views.weekly_report_view, name='weekly_report_view'),
    path('report/sales/', views.sales_report_view, name='sales_report_view'),
    path('report/audit/', views.product_audit_log_view, name='product_audit_log_view'),
    path('audit-log/export-csv/', views.export_audit_log_csv, name='export_audit_log_csv'),
    path('export/orders/csv/', views.export_orders_csv, name='export_orders_csv'),
    path('director-dashboard/', views.director_dashboard, name='director_dashboard'),
    path('orders/calculator/all/', views.order_calculator_list, name='order_calculator_list'),
    path('material_report/', views.material_sarfi_report, name='material_report'),
    path('debts/', views.debt_report, name='debt_report'),
    path('add-payment/<int:order_id>/', views.add_prepayment, name='add_prepayment'),
    path('rating/', views.customer_rating, name='customer_rating'),
    path('get-customer-orders/<str:customer_id>/', views.get_customer_orders, name='get_customer_orders'),
    
    # ==================== DRIVER VA QOROVUL ====================
    path('driver/dashboard/', views.driver_dashboard, name='driver_dashboard'),
    path('track-location/', views.track_location, name='track_location'),
    path('guard/', views.guard_dashboard, name='guard_dashboard'),
    path('patrol/', views.guard_patrol_view, name='guard_patrol'),
    
    # ==================== KONSTRUKTOR (CHIZMA) ====================
    path('', views.constructor_index, name='index'),
    path('calculator/', views.constructor_index, name='calculator'),
    path('chizma/', views.constructor_index, name='chizma'),
    path('projects/', views.project_list, name='project_list'),
    path('projects/create/', views.project_create, name='project_create'),
    path('projects/<int:pk>/', views.project_detail, name='project_detail'),
    path('projects/<int:pk>/edit/', views.project_edit, name='project_edit'),
    path('projects/<int:pk>/delete/', views.project_delete, name='project_delete'),
    path('projects/<int:pk>/ai/', views.ai_recommendation, name='ai_recommendation'),
    path('projects/<int:pk>/send/', views.send_report, name='send_report'),
    path('projects/<int:pk>/create-order/', views.create_order_from_project, name='create_order'),
    path('projects/<int:pk>/download-svg/', views.download_svg, name='download_svg'),
    
    # ==================== KASSA (YAGONA VA TO'LIQ) ====================
    path('cash/management/', views.cash_management, name='cash_management'),
    path('cash/transaction/create/', views.cash_transaction_create, name='cash_transaction_create'),
    path('cash/transactions/', views.cash_transaction_list, name='cash_transaction_list'),
    path('cash/transaction/<str:transaction_id>/json/', views.cash_transaction_json, name='cash_transaction_json'),
    path('cash/transaction/<str:transaction_id>/edit/', views.cash_transaction_edit, name='cash_transaction_edit'),
    path('cash/transaction/<str:transaction_id>/delete/', views.cash_transaction_delete, name='cash_transaction_delete'),
    path('cash/daily-report/create/', views.daily_report_create, name='daily_report_create'),
    path('cash/daily-report/<int:pk>/', views.daily_report_detail, name='daily_report_detail'),
    path('cash/daily-reports/', views.daily_report_list, name='daily_report_list'),
    path('cash/daily-report/<int:pk>/json/', views.daily_report_json, name='daily_report_json'),
    path('cash/export/excel/', views.export_cash_report_excel, name='export_cash_report_excel'),
    path('cash/api/stats/', views.cash_api_stats, name='cash_api_stats'),
    path('cash/api/transactions/', views.cash_api_transactions, name='cash_api_transactions'),
    path('cash/api/transaction/create/', views.cash_api_transaction_create, name='cash_api_transaction_create'),
    path('cash/api/daily-report/create/', views.cash_api_daily_report_create, name='cash_api_daily_report_create'),
    
    # ==================== TASHQI TO'LOVLAR ====================
    path('order/<int:order_id>/payment/click/', views.order_payment_click, name='order_payment_click'),
    path('order/<int:order_id>/payment/payme/', views.order_payment_payme, name='order_payment_payme'),
    # urls.py ga qo'shimcha
    path('export-sales-excel/', views.export_sales_report_excel, name='export_sales_excel'),
    # ==================== OMBORCHI UCHUN ====================
    path('order/receive-warehouse/<int:pk>/', views.order_receive_warehouse, name='order_receive_warehouse'),
]
