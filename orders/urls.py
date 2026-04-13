# orders/urls.py
from django.urls import path
from . import views
from django.contrib.auth.views import LogoutView # <--- LogoutView import qilingan

urlpatterns = [
    # Kirish va Chiqish (Tuzatildi!)
    # 1. Login sahifasi (Custom view)
    path('login/', views.CustomLoginView.as_view(), name='login'), # Bu sizning /login/ manzilingiz
    
    # 2. Logout sahifasi (LogoutView orqali qo'shildi)
    # Tizimdan chiqishni amalga oshiradi va LOGOUT_REDIRECT_URL ga yo'naltiradi.
    path('logout/', LogoutView.as_view(next_page='/login/'), name='logout'), # <--- O'ZGARISH BU YERDA

    # Asosiy Boshqaruv
    path('', views.order_list, name='order_list'),
    path('warehouse/add/', views.add_material, name='add_material'),
    path('material/<int:material_id>/edit/', views.edit_material, name='edit_material'),
    path('material/<int:material_id>/delete/', views.delete_material, name='delete_material'),
    # Eski: path('material/<int:material_id>/output/', views.material_output, name='material_output'),
# Yangi (ID-siz):
    path('material/output/', views.material_output, name='material_output'),
    path('outputs/history/', views.output_history, name='output_history'), 
    # Buyurtma Operatsiyalari
    path('create/', views.order_create, name='order_create'),
    path('edit/<int:pk>/', views.order_edit, name='order_edit'),
    path('outputs/export/excel/', views.export_outputs_excel, name='export_outputs_excel'),
    path('inventory/export/excel/', views.export_inventory_excel, name='export_inventory_excel'),
    path('delete/<int:pk>/', views.order_delete, name='order_delete'),
    path('import-excel/', views.import_excel_api, name='import_excel_api'),

    # Bosqichlar
    path('confirm/<int:pk>/', views.order_confirm, name='order_confirm'),
    path('reject/<int:pk>/', views.order_reject, name='order_reject'),
    path('start/<int:pk>/', views.order_start_production, name='order_start_production'),
    path('finish/<int:pk>/', views.order_finish, name='order_finish'),
    path('complete/<int:pk>/', views.order_complete, name='order_complete'),

    # Hisobotlar
    path('report/weekly/', views.weekly_report_view, name='weekly_report_view'),
    path('report/sales/', views.sales_report_view, name='sales_report_view'),
    path('export/orders/csv/', views.export_orders_csv, name='export_orders_csv'),
    path('director-dashboard/', views.director_dashboard, name='director_dashboard'),
    
    # AUDIT LOG
    path('orders/calculator/all/', views.order_calculator_list, name='order_calculator_list'),
    path('report/audit/', views.product_audit_log_view, name='product_audit_log_view'),
    path('audit-log/export-csv/', views.export_audit_log_csv, name='export_audit_log_csv'), 

    # Tafsilotlar va Rasm yuklash
    path('detail/<int:pk>/', views.order_detail, name='order_detail'),
    path('upload-order-image/', views.upload_order_image, name='upload_order_image'), 

    # Ustalar harakatlari
    path('order/<int:pk>/worker-accept/', views.order_worker_accept, name='order_worker_accept'),
    path('order/<int:pk>/worker-start/', views.order_worker_start, name='order_worker_start'),
    path('order/<int:pk>/worker-finish/', views.order_worker_finish, name='order_worker_finish'),

    path('worker-panel/', views.worker_panel, name='worker_panel'),
    path('worker-orders/<int:worker_id>/', views.worker_orders, name='worker_orders'),
    # Ham eski, ham yangi nom bilan ishlashi uchun:
    # path('worker-panel/', views.worker_panel, name='worker_panel'),
    path('worker-my-orders/', views.worker_panel, name='worker_my_orders'), # SHUNI QO'SHING
    path('worker-report/', views.worker_activity_report_view, name='worker_activity_report'),

    # Duplicate yo'llar olib tashlandi, lekin quyidagilar qoldirildi
    # path('worker-report/', views.worker_activity_report_view, name='worker_activity_report'),
    path('driver/dashboard/', views.driver_dashboard, name='driver_dashboard'),
    
    # Koordinatalarni fonda qabul qilish (POST so'rovlar uchun)
    path('track-location/', views.track_location, name='track_location'),
    path('archive/', views.order_archive, name='order_archive'),
    # EKSPORT YO'LI
    path('worker-report/export-csv/', views.export_worker_activity_csv, name='export_worker_activity_csv'),
    path('material_report/', views.material_sarfi_report, name='material_report'),
    path('guard/', views.guard_dashboard, name='guard_dashboard'),
    path('patrol/', views.guard_patrol_view, name='guard_patrol'),

    path('debts/', views.debt_report, name='debt_report'),
    path('add-payment/<int:order_id>/', views.add_prepayment, name='add_prepayment'),
    path('rating/', views.customer_rating, name='customer_rating'), # SHU QATORNI TEKSHIRING
    path('get-customer-orders/<str:customer_id>/', views.get_customer_orders, name='get_customer_orders'),
    # 2. Harakatlar
    path('transactions/add/', views.add_transaction_view, name='add_transaction'),
    path('transactions/remove/', views.remove_transaction_view, name='remove_transaction'),

    # 3. Inventarizatsiya
    path('inventory/list/', views.material_list, name='material_list'),
    path('rankings/', views.rankings_view, name='ranking'), # SHU QATORNI QO'SHING
    

    path('warehouse/', views.warehouse_dashboard, name='warehouse_dashboard'),
    # 4. Boshqa inventarizatsiya harakatlari
    path('inventory/transaction/create/', views.material_transaction_create, name='material_transaction_create'),
    path('fast-scanner/', views.fast_scanner_view, name='fast_scanner'),
    path('api/find-material/', views.find_material_by_code_api, name='find_material_api'),
    path('api/save-scanned-transactions/', views.save_scanned_transactions_api, name='save_scanned_transactions_api'),
]




# Agar mavjud bo'lsa, "Xomashyolar" kategoriyasini olamiz
