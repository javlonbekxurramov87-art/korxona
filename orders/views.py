from email.mime import image

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import Group 
from django.contrib import messages 
from django.contrib.auth import get_user_model 
from django.db.models import Q, Sum 
from orders.models import Worker, Order     
from datetime import date, timedelta, datetime
from django.http import HttpResponse, JsonResponse, HttpResponseForbidden
import csv 
from django.urls import reverse_lazy
from django.contrib.auth.views import LoginView
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .models import Material
from django.db.models import Sum, F
from django.conf import settings

# AUDIT LOG UCHUN IMPORTLAR
from django.contrib.contenttypes.models import ContentType
from django.contrib.admin.models import LogEntry, CHANGE, DELETION, ADDITION 

from .models import Order, Notification, Worker 
from .forms import OrderForm, StartImageUploadForm, FinishImageUploadForm, MaterialForm

from django.db.models import Count, Case, When, IntegerField


User = get_user_model()

# --- Yordamchi Funksiya: Foydalanuvchi qaysi guruhda ekanligini tekshirish ---
def is_in_group(user, group_name):
    """Foydalanuvchi berilgan guruhda mavjudligini tekshiradi."""
    if user.is_anonymous:
        return False
        
    if user.is_superuser and group_name == 'Glavniy Admin':
        return True
        
    try:
        return user.groups.filter(name=group_name).exists()
    except Group.DoesNotExist:
        return False

# --- Yangi: Kuzatuvchi funksiyalari ---
def is_observer(user):
    """Foydalanuvchi kuzatuvchi guruhida ekanligini tekshiradi."""
    if user.is_anonymous:
        return False
    return is_in_group(user, 'Kuzatuvchi')

def is_observer_or_above(user):
    """Kuzatuvchi yoki undan yuqori darajadagi foydalanuvchilarni tekshiradi."""
    return (
        is_observer(user) or 
        is_in_group(user, 'Glavniy Admin') or 
        is_in_group(user, 'Menejer') or 
        is_in_group(user, "Ishlab Chiqarish Boshlig'i") or
        user.is_superuser
    )

# ----------------------------------------------------------------------
# 💡 YORDAMCHI FUNKSIYA: MUDDAT BUZILISHINI TEKSHIRISH
# ----------------------------------------------------------------------
def check_and_create_overdue_alerts(order):
    """
    Berilgan buyurtma uchun muddat o'tgan bo'lsa va ogohlantirish yuborilmagan bo'lsa,
    Notification yaratadi.
    """
    if not order.deadline or order.deadline > timezone.now():
        return False 
    
    if order.status in ['BAJARILDI', 'RAD_ETILDI', 'TAYYOR']:
        return False 

    if hasattr(order, 'deadline_breach_alert_sent') and order.deadline_breach_alert_sent:
        return False 

    admin_users = User.objects.filter(
        Q(is_superuser=True) | 
        Q(groups__name='Glavniy Admin') | 
        Q(groups__name="Ishlab Chiqarish Boshlig'i")
    ).distinct()
    
    message = (
        f"🚨 URGENT: Buyurtma #{order.order_number} ning muddati {order.deadline.strftime('%d-%m %H:%M')} da O'TIB KETDI. "
        f"Status: {order.get_status_display()}."
    )
    
    for admin in admin_users:
        Notification.objects.create(
            user=admin,
            order=order,
            message=message
        )
    
    if hasattr(order, 'deadline_breach_alert_sent'):
        order.deadline_breach_alert_sent = True
        order.save(update_fields=['deadline_breach_alert_sent'])
    
    return True

# --- Yordamchi Funksiya: Hisobotni ko'rishga ruxsatni tekshirish ---
def is_report_viewer(user):
    """Admin, Menejer va Ishlab Chiqarish Boshlig'iga ruxsat beradi."""
    from django.conf import settings
    
    # Agar foydalanuvchi tizimga kirmagan bo'lsa, avtomatik rad etamiz
    if not user.is_authenticated:
        return False
        
    return (
        is_in_group(user, 'Glavniy Admin') or 
        is_in_group(user, 'Menejer') or 
        is_in_group(user, "Ishlab Chiqarish Boshlig'i")
    )

# --- Yangi: Hisobotlar uchun kengaytirilgan ruxsat tekshiruvi ---
def is_report_viewer_or_observer(user):
    """Admin, Menejer, Ishlab Chiqarish Boshlig'i yoki Kuzatuvchiga ruxsat beradi."""
    return is_report_viewer(user) or is_observer(user)

# ----------------------------------------------------------------------
# YANGI: RASM YUKLASH FUNKSIYASI
# ----------------------------------------------------------------------
@require_POST
@csrf_exempt
@login_required
def upload_order_image(request):
    """
    Usta tomonidan rasm yuklash uchun AJAX endpoint
    """
    try:
        order_id = request.POST.get('order_id')
        upload_type = request.POST.get('upload_type')  # 'qabul', 'start', 'finish'
        comment = request.POST.get('comment', '')
        
        if not order_id or not upload_type:
            return JsonResponse({'success': False, 'error': 'Ma\'lumotlar yetarli emas'})
        
        try:
            order = Order.objects.get(pk=order_id)
        except Order.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Buyurtma topilmadi'})
        
        # Ruxsatni tekshirish
        is_worker = is_in_group(request.user, 'Usta')
        is_assigned_worker = order.assigned_workers.filter(user=request.user).exists()
        
        if not is_worker or not is_assigned_worker:
            return JsonResponse({'success': False, 'error': 'Sizga ruxsat yo\'q'})
        
        # Rasm yuklash turiga qarab formani tanlash
        if upload_type == 'start':
            form = StartImageUploadForm(request.POST, request.FILES, instance=order)
            if order.status == 'TASDIQLANDI' and not order.start_image:
                if form.is_valid():
                    order = form.save(commit=False)
                    order.status = 'USTA_QABUL_QILDI'
                    order.start_image_uploaded_at = timezone.now()
                    if comment:
                        order.comment = f"{order.comment or ''}\n\nUsta izohi ({timezone.now().strftime('%Y-%m-%d %H:%M')}): {comment}"
                    order.save()
                    return JsonResponse({
                        'success': True, 
                        'message': 'Boshlash rasmi muvaffaqiyatli yuklandi',
                        'new_status': order.status
                    })
                else:
                    return JsonResponse({
                        'success': False, 
                        'error': form.errors.as_text()
                    })
            else:
                return JsonResponse({'success': False, 'error': 'Boshlash rasm yuklash uchun holat mos emas'})
                
        elif upload_type == 'finish':
            form = FinishImageUploadForm(request.POST, request.FILES, instance=order)
            if order.status in ['USTA_BOSHLA', 'ISHDA'] and not order.finish_image:
                if form.is_valid():
                    order = form.save(commit=False)
                    order.status = 'USTA_TUGATDI'
                    order.worker_finished_at = timezone.now()
                    order.finish_image_uploaded_at = timezone.now()
                    if comment:
                        order.comment = f"{order.comment or ''}\n\nUsta izohi ({timezone.now().strftime('%Y-%m-%d %H:%M')}): {comment}"
                    order.save()
                    return JsonResponse({
                        'success': True, 
                        'message': 'Tugatish rasmi muvaffaqiyatli yuklandi',
                        'new_status': order.status
                    })
                else:
                    return JsonResponse({
                        'success': False, 
                        'error': form.errors.as_text()
                    })
            else:
                return JsonResponse({'success': False, 'error': 'Tugatish rasm yuklash uchun holat mos emas'})
        else:
            return JsonResponse({'success': False, 'error': 'Noto\'g\'ri yuklash turi'})
            
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

# ----------------------------------------------------------------------
# MAXSUS LOGIN VIEW
# ----------------------------------------------------------------------
class CustomLoginView(LoginView):
    template_name = 'orders/login.html'
    redirect_authenticated_user = True
    success_url = reverse_lazy('order_list') 

# ----------------------------------------------------------------------
# ASOSIY SAHIFA / RO'YXAT
# ----------------------------------------------------------------------
@login_required 
def order_list(request):
    user = request.user
    now = timezone.now()
    
    # ================================================================
    # 1. GURUHLAR TEKSHIRUVI (BIR MARTA)
    # ================================================================
    is_glavniy_admin = user.is_superuser or is_in_group(user, 'Glavniy Admin')
    is_production_boss = is_in_group(user, "Ishlab Chiqarish Boshlig'i")
    is_manager = is_in_group(user, 'Menejer/Tasdiqlovchi')
    is_worker = is_in_group(user, 'Usta')
    is_observer = is_in_group(user, 'Kuzatuvchi')
    is_sales_manager = is_in_group(user, 'Sales Manager') or user.username.lower() == 'sales_manager'  # <-- YANGI QATOR
    
    # ================================================================
    # 2. FILTR PARAMETRLARI
    # ================================================================
    search_query = request.GET.get('q', '').strip()
    filter_type = request.GET.get('filter', 'all')
    page_number = request.GET.get('page', 1)
    
    # ================================================================
    # 3. ARXIV BUYURTMALAR (OPTIMALLASHTIRILGAN)
    # ================================================================
    archived_orders_qs = Order.objects.filter(
        status__in=['BAJARILDI', 'USTA_TUGATDI', 'TAYYOR']
    ).select_related('customer').prefetch_related('assigned_workers__user')
    
    if is_worker and not (is_glavniy_admin or is_production_boss or is_manager or is_observer):
        archived_orders_qs = archived_orders_qs.filter(assigned_workers__user=user).distinct()
    
    archived_count = archived_orders_qs.count()
    
    # ================================================================
    # 4. FAOL BUYURTMALAR - SELECT_RELATED VA PREFETCH_RELATED
    # ================================================================
    base_qs = Order.objects.select_related('parent_order').prefetch_related(
        'assigned_workers__user'
    ).exclude(status__in=['BAJARILDI', 'USTA_TUGATDI', 'TAYYOR'])
    
    # ================================================================
    # 5. QIDIRUV FILTRI
    # ================================================================
    if search_query:
        search_filter = (
            Q(order_number__icontains=search_query) |
            Q(customer_name__icontains=search_query) |
            Q(product_name__icontains=search_query) |
            Q(customer_unique_id__icontains=search_query)
        )
        base_qs = base_qs.filter(search_filter)
    
    # ================================================================
    # 6. ROL BO'YICHA FILTR (AGENT UCHUN)
    # ================================================================
    if is_worker and not (is_glavniy_admin or is_production_boss or is_manager or is_observer):
        base_qs = base_qs.filter(assigned_workers__user=user).exclude(status='RAD_ETILDI').distinct()
    
    # ================================================================
    # 7. ORDER TIPINI ANNOTATSIYA QILISH (BIR SO'ROVDA)
    # ================================================================
    from django.db.models import Case, When, Value, CharField
    
    orders_with_type = base_qs.annotate(
        order_type=Case(
            When(parent_order__isnull=True, then=Value('MAIN')),
            When(
                Q(product_name__icontains='panel') | 
                Q(product_name__icontains='панель') |
                Q(product_name__icontains='панел'), 
                then=Value('PANEL_CHILD')
            ),
            When(
                Q(product_name__icontains='ugul') | 
                Q(product_name__icontains='угол') |
                Q(product_name__icontains='уголь'), 
                then=Value('UGUL_CHILD')
            ),
            default=Value('OTHER_CHILD'),
            output_field=CharField()
        )
    ).order_by('-created_at')
    
    # ================================================================
    # 8. FILTRLASH TURLARI (BIR MARTA QO'LLASH)
    # ================================================================
    filter_conditions = {
        'completed': Q(status__in=['TAYYOR', 'BAJARILDI']),
        'in_progress': ~Q(status__in=['TAYYOR', 'BAJARILDI', 'RAD_ETILDI']) & (Q(deadline__isnull=True) | Q(deadline__gte=now)),
        'overdue': Q(deadline__lt=now) & ~Q(status__in=['BAJARILDI', 'RAD_ETILDI', 'TAYYOR']),
    }
    
    if filter_type in filter_conditions:
        orders_with_type = orders_with_type.filter(filter_conditions[filter_type])
    
    # ================================================================
    # 9. PAGINATION (50 TA)
    # ================================================================
    from django.core.paginator import Paginator
    paginator = Paginator(orders_with_type, 50)
    page_obj = paginator.get_page(page_number)
    
    # ================================================================
    # 10. GURUHLASH (PYTHON DA - TEZ)
    # ================================================================
    main_orders = []
    panel_child_orders = []
    ugul_child_orders = []
    other_child_orders = []
    
    for order in page_obj:
        if order.order_type == 'MAIN':
            main_orders.append(order)
        elif order.order_type == 'PANEL_CHILD':
            panel_child_orders.append(order)
        elif order.order_type == 'UGUL_CHILD':
            ugul_child_orders.append(order)
        else:
            other_child_orders.append(order)
    
    # ================================================================
    # 11. STATISTIKA (BIR AGGREGATE SO'ROV)
    # ================================================================
    from django.db.models import Count, Sum, F
    
    main_qs = Order.objects.filter(parent_order__isnull=True)
    if search_query:
        main_qs = main_qs.filter(search_filter)
    
    if is_worker and not (is_glavniy_admin or is_production_boss or is_manager or is_observer):
        main_qs = main_qs.filter(assigned_workers__user=user).exclude(status='RAD_ETILDI')
    
    stats = main_qs.aggregate(
        total_orders=Count('id'),
        completed_orders=Count('id', filter=Q(status__in=['TAYYOR', 'BAJARILDI'])),
        in_progress_orders=Count('id', filter=~Q(status__in=['TAYYOR', 'BAJARILDI', 'RAD_ETILDI']) & (Q(deadline__isnull=True) | Q(deadline__gte=now))),
        overdue_orders_count=Count('id', filter=Q(deadline__lt=now) & ~Q(status__in=['BAJARILDI', 'RAD_ETILDI', 'TAYYOR'])),
    )
    
    # ================================================================
    # 12. CHILD ORDERLAR STATISTIKASI (BIR SO'ROV)
    # ================================================================
    child_stats = Order.objects.filter(parent_order__isnull=False).aggregate(
        all_child_orders_count=Count('id'),
        panel_child_count=Count('id', filter=Q(product_name__icontains='panel') | Q(product_name__icontains='панель') | Q(product_name__icontains='панел')),
        ugul_child_count=Count('id', filter=Q(product_name__icontains='ugul') | Q(product_name__icontains='угол') | Q(product_name__icontains='уголь')),
        panel_completed=Count('id', filter=(Q(product_name__icontains='panel') | Q(product_name__icontains='панель') | Q(product_name__icontains='панел')) & Q(status__in=['TAYYOR', 'BAJARILDI'])),
        ugul_completed=Count('id', filter=(Q(product_name__icontains='ugul') | Q(product_name__icontains='угол') | Q(product_name__icontains='уголь')) & Q(status__in=['TAYYOR', 'BAJARILDI'])),
    )
    
    # ================================================================
    # 13. TO'LANMAGAN BUYURTMALAR (DATABASE DA HISOBLASH)
    # ================================================================
    unpaid_orders = Order.objects.none()
    total_unpaid_amount = 0
    unpaid_orders_count = 0
    
    if is_glavniy_admin or is_manager:
        unpaid_orders = Order.objects.filter(
            parent_order__isnull=True,
            total_price__gt=F('prepayment')
        ).exclude(status='BEKOR_QILINDI').only('order_number', 'customer_name', 'total_price', 'prepayment')
        
        if is_worker and not is_glavniy_admin and not is_manager:
            unpaid_orders = Order.objects.none()
        else:
            unpaid_orders_count = unpaid_orders.count()
            total_unpaid_amount = unpaid_orders.aggregate(
                total=Sum(F('total_price') - F('prepayment'))
            )['total'] or 0
    
    # ================================================================
    # 14. NOTIFICATIONLAR
    # ================================================================
    user_notifications = Notification.objects.filter(user=user, is_read=False)[:5]
    
    # ================================================================
    # 15. MUDDAT BUZILISHINI TEKSHIRISH (FAQAT ADMINLAR UCHUN)
    # ================================================================
    if is_glavniy_admin or is_production_boss:
        overdue_check_orders = main_orders[:20]  # Faqat 20 tasini tekshirish
        for order in overdue_check_orders:
            if order.deadline and order.deadline < now and order.status not in ['BAJARILDI', 'RAD_ETILDI', 'TAYYOR']:
                check_and_create_overdue_alerts(order)
    
    # ================================================================
    # 16. MIJOZLAR SONI (KESH YOKI BIR SO'ROV)
    # ================================================================
    customers_count = Order.objects.values('customer_unique_id').distinct().count()
    
    # ================================================================
    # 17. PROGRESS FOIZLARI
    # ================================================================
    panel_progress_percentage = 0
    other_progress_percentage = 0
    panel_in_progress = 0
    other_in_progress = 0
    
    if child_stats['panel_child_count'] > 0:
        panel_progress_percentage = (child_stats['panel_completed'] / child_stats['panel_child_count']) * 100
        panel_in_progress = child_stats['panel_child_count'] - child_stats['panel_completed']
    
    if child_stats['ugul_child_count'] > 0:
        other_progress_percentage = (child_stats['ugul_completed'] / child_stats['ugul_child_count']) * 100
        other_in_progress = child_stats['ugul_child_count'] - child_stats['ugul_completed']
    
    # ================================================================
    # 18. CONTEXT
    # ================================================================
    context = {
        # Pagination
        'page_obj': page_obj,
        'orders': page_obj,
        
        # Guruhlangan orderlar
        'main_orders': main_orders,
        'panel_child_orders': panel_child_orders,
        'ugul_child_orders': ugul_child_orders,
        'other_child_orders': other_child_orders,
        
        # Arxiv
        'archived_count': archived_count,
        'archived_orders': archived_orders_qs[:100],
        
        # To'lanmaganlar
        'unpaid_orders_count': unpaid_orders_count,
        'total_unpaid_amount': total_unpaid_amount,
        
        # Rollar
        'is_glavniy_admin': is_glavniy_admin,
        'is_manager': is_manager,
        'is_production_boss': is_production_boss,
        'is_worker': is_worker,
        'is_observer': is_observer,
        'is_sales_manager': is_sales_manager,  # <-- YANGI QATOR
        'is_storekeeper': user.username.lower() == 'omborchi' or 'store' in user.username.lower(),
        'can_view_orders': any([is_glavniy_admin, is_production_boss, is_manager, is_worker, is_observer]),
        
        # Filtrlar
        'search_query': search_query,
        'filter_type': filter_type,
        'now': now,
        
        # Statistikalar
        'total_orders': stats['total_orders'],
        'completed_orders': stats['completed_orders'],
        'in_progress_orders': stats['in_progress_orders'],
        'overdue_orders_count': stats['overdue_orders_count'],
        
        # Child statistikalar
        'all_child_orders_count': child_stats['all_child_orders_count'],
        'panel_child_count': child_stats['panel_child_count'],
        'ugul_child_count': child_stats['ugul_child_count'],
        'other_child_count': child_stats['all_child_orders_count'] - child_stats['panel_child_count'] - child_stats['ugul_child_count'],
        'panel_completed': child_stats['panel_completed'],
        'ugul_completed': child_stats['ugul_completed'],
        'panel_in_progress': panel_in_progress,
        'other_in_progress': other_in_progress,
        'panel_progress_percentage': round(panel_progress_percentage, 1),
        'other_progress_percentage': round(other_progress_percentage, 1),
        
        # Boshqa
        'customers_count': customers_count,
        'notifications': user_notifications,
    }
    
    return render(request, 'orders/order_list.html', context)




from django.db.models import Q
from django.core.paginator import Paginator
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .models import Order  # ChildOrder ni bu yerdan olib tashladik

# Agar is_in_group funksiyasi boshqa joyda bo'lsa, uni import qiling yoki shu yerga yozing
def is_in_group(user, group_name):
    return user.groups.filter(name=group_name).exists()

@login_required
def order_archive(request):
    """Arxivlangan (bajarilgan) buyurtmalar ro'yxati"""
    
    # Foydalanuvchi rollari
    is_glavniy_admin = request.user.is_superuser or is_in_group(request.user, 'Glavniy Admin')
    is_manager = is_in_group(request.user, 'Menejer/Tasdiqlovchi')
    is_worker = is_in_group(request.user, 'Usta') or is_in_group(request.user, 'Eshik Ustasi')
    
    # Arxivlangan statuslar
    archived_statuses = ['BAJARILDI', 'USTA_TUGATDI', 'TAYYOR']
    
    # Asosiy queryset
    main_orders = Order.objects.filter(
        status__in=archived_statuses
    ).order_by('-worker_finished_at', '-created_at')
    
    # Filtrlar
    search_query = request.GET.get('q', '')
    worker_filter = request.GET.get('worker_type', '')  # list, panel, eshik, ugol
    
    # USTA TURI BO'YICHA FILTR (userlar bo'yicha)
    if not is_worker and worker_filter:
        # Usta username'larini aniqlash
        worker_usernames = {
            'list': 'list_usta',
            'panel': 'panel_usta',
            'eshik': 'eshik_usta',
            'ugol': 'ugol_usta'
        }
        
        if worker_filter in worker_usernames:
            username = worker_usernames[worker_filter]
            from django.contrib.auth.models import User
            try:
                worker_user = User.objects.get(username=username)
                main_orders = main_orders.filter(
                    assigned_workers__user=worker_user
                ).distinct()
            except User.DoesNotExist:
                main_orders = main_orders.none()
    
    # USTA UCHUN FILTR - o'z orderlari
    elif is_worker and not (is_glavniy_admin or is_manager):
        main_orders = main_orders.filter(
            assigned_workers__user=request.user
        ).distinct()
    
    # Qidiruv
    if search_query:
        main_orders = main_orders.filter(
            Q(order_number__icontains=search_query) |
            Q(customer_name__icontains=search_query) |
            Q(product_name__icontains=search_query) |
            Q(customer_unique_id__icontains=search_query)
        )
    
    # Pagination
    paginator = Paginator(main_orders, 12)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # STATISTIKA (admin/menejer uchun) - userlar bo'yicha
    worker_stats = {'list': 0, 'panel': 0, 'eshik': 0, 'ugol': 0}
    if not is_worker:
        from django.contrib.auth.models import User
        worker_usernames = {
            'list': 'list_usta',
            'panel': 'panel_usta',
            'eshik': 'eshik_usta',
            'ugol': 'ugol_usta'
        }
        for key, username in worker_usernames.items():
            try:
                worker_user = User.objects.get(username=username)
                count = Order.objects.filter(
                    status__in=archived_statuses,
                    assigned_workers__user=worker_user
                ).distinct().count()
                worker_stats[key] = count
            except User.DoesNotExist:
                worker_stats[key] = 0
    
    # USTA STATISTIKASI
    personal_stats = {}
    if is_worker:
        worker_orders = Order.objects.filter(
            assigned_workers__user=request.user,
            status__in=archived_statuses
        ).distinct()
        personal_stats = {
            'total': worker_orders.count(),
            'bajarildi': worker_orders.filter(status='BAJARILDI').count(),
            'usta_tugatdi': worker_orders.filter(status='USTA_TUGATDI').count(),
            'tayyor': worker_orders.filter(status='TAYYOR').count(),
        }
    
    context = {
        'main_orders': page_obj,
        'main_orders_count': main_orders.count(),
        'search_query': search_query,
        'worker_filter': worker_filter,
        'is_worker': is_worker,
        'is_manager': is_manager,
        'is_glavniy_admin': is_glavniy_admin,
        'worker_stats': worker_stats,
        'personal_stats': personal_stats,
    }
    
    return render(request, 'orders/order_archive.html', context)

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import DriverTrip, TripPoint
from django.utils import timezone

@login_required
def driver_dashboard(request):
    # Faqat haydovchi yoki admin kira oladi
    is_driver = "haydovchi" in request.user.username.lower() or request.user.is_staff
    if not is_driver:
        messages.error(request, "Kirish taqiqlangan!")
        return redirect('home')

    # Faqat SHU haydovchiga tegishli oxirgi faol reys
    active_trip = DriverTrip.objects.filter(
        driver=request.user, 
        is_active=True
    ).last()

    # Haydovchining oxirgi 5 ta yopilgan reysi (Tarixi)
    trip_history = DriverTrip.objects.filter(
        driver=request.user, 
        is_active=False
    ).order_by('-start_time')[:5]

    return render(request, 'orders/driver_dashboard.html', {
        'active_trip': active_trip,
        'trip_history': trip_history
    })
@csrf_exempt
@login_required
def track_location(request):
    if request.method == "POST":
        data = json.loads(request.body)
        lat = data.get('lat')
        lng = data.get('lng')
        
        # Faol reysni qidirish yoki yangisini yaratish
        trip, created = DriverTrip.objects.get_or_create(
            driver=request.user, 
            is_active=True,
            defaults={'car_number': "MASHINA-01"} # Buni profilidan olsa ham bo'ladi
        )
        
        # Yangi nuqtani saqlash
        last_point = trip.points.last()
        is_stop = False
        
        if last_point:
            # Agar oxirgi nuqtadan beri 3 minut o'tgan bo'lsa va masofa o'zgarmagan bo'lsa
            # (Bu yerda mantiqni kengaytirish mumkin)
            pass

        TripPoint.objects.create(
            trip=trip,
            latitude=lat,
            longitude=lng,
            is_stop=is_stop
        )
        
        return JsonResponse({"status": "ok", "trip_id": trip.id})

# orders/views.py
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.admin.views.decorators import staff_member_required
from .models import Order, Material, MaterialTransaction


from django.db.models import Count, Sum, F, Q
from decimal import Decimal
from django.core.paginator import Paginator

@login_required
@staff_member_required
def warehouse_dashboard(request):
    """Ombordagi barcha materiallar qoldig'i - Kategoriyalar bilan"""
    
    # 1. Barcha kategoriyalarni olish (materiallar soni va umumiy qoldiq bilan)
    categories = Category.objects.annotate(
        material_count=Count('material'),
        total_quantity=Coalesce(Sum('material__quantity'), Decimal('0'))
    ).order_by('name')
    
    # 2. Tanlangan kategoriya (GET parametridan)
    selected_category = request.GET.get('category', '')
    search_query = request.GET.get('search', '')
    status_filter = request.GET.get('status', '')
    
    # 3. Materiallarni olish (select_related bilan optimallashtirilgan)
    materials = Material.objects.select_related('category').all()
    
    # 4. Kategoriya bo'yicha filtr
    if selected_category:
        materials = materials.filter(category__name=selected_category)
    
    # 5. Qidiruv bo'yicha filtr
    if search_query:
        materials = materials.filter(
            Q(name__icontains=search_query) |
            Q(category__name__icontains=search_query) |
            Q(product_name__icontains=search_query)
        )
    
    # 6. Holat bo'yicha filtr (kam qolgan / yetarli)
    if status_filter == 'danger':
        materials = materials.filter(quantity__lte=F('min_stock_level'))
    elif status_filter == 'success':
        materials = materials.filter(quantity__gt=F('min_stock_level'))
    
    # 7. Tartiblash
    materials = materials.order_by('name')
    
    # 8. Pagination (har bir sahifada 20 ta)
    paginator = Paginator(materials, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # 9. Statistik ma'lumotlar
    total_materials = Material.objects.count()
    total_quantity = Material.objects.aggregate(total=Sum('quantity'))['total'] or Decimal('0')
    low_stock_count = Material.objects.filter(quantity__lte=F('min_stock_level')).count()
    
    context = {
        # Kategoriyalar
        'categories': categories,
        'selected_category': selected_category,
        'total_categories': categories.count(),
        
        # Materiallar
        'materials': page_obj,
        'page_obj': page_obj,
        
        # Filtrlar
        'search_query': search_query,
        'status_filter': status_filter,
        
        # Statistikalar
        'total_materials': total_materials,
        'total_quantity': total_quantity,
        'low_stock_count': low_stock_count,
    }
    
    return render(request, 'orders/warehouse_dashboard.html', context)
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import Material, Category

@login_required
@staff_member_required
def edit_material(request, material_id):
    """Materialni tahrirlash"""
    material = get_object_or_404(Material, id=material_id)
    
    if request.method == "POST":
        name = request.POST.get('name', '').strip()
        quantity = float(request.POST.get('quantity', 0))
        unit = request.POST.get('unit', '')
        min_stock = float(request.POST.get('min_stock', 0))
        
        # Kategoriya yangilash (agar kerak bo'lsa)
        category_name = request.POST.get('category_name', '')
        if category_name:
            category_obj, _ = Category.objects.get_or_create(name=category_name.strip())
            material.category = category_obj
        
        material.name = name
        material.quantity = quantity
        material.unit = unit
        material.min_stock_level = min_stock
        material.save()
        
        messages.success(request, f'"{material.name}" muvaffaqiyatli tahrirlandi!')
        return redirect('orders:warehouse_dashboard')
    
    return render(request, 'orders/edit_material.html', {'material': material})


@login_required
@staff_member_required
def delete_material(request, material_id):
    """Materialni o'chirish"""
    material = get_object_or_404(Material, id=material_id)
    material_name = material.name
    
    if request.method == "POST":
        material.delete()
        messages.success(request, f'"{material_name}" muvaffaqiyatli o\'chirildi!')
    
    return redirect('orders:warehouse_dashboard')

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from .models import Material, MaterialOutput, Category
import json

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import Material, MaterialOutput, Category
from django.shortcuts import render, redirect, get_object_or_404 # Mana buni tekshiring

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from decimal import Decimal
from .models import Material, MaterialOutput

@login_required
@staff_member_required
def material_output(request):
    """Materialni ombordan chiqarish - To'liq to'g'rilangan variant"""
    
    if request.method == "POST":
        m_id = request.POST.get('material_id')
        
        if not m_id:
            messages.error(request, "Iltimos, materialni ro'yxatdan tanlang!")
            return redirect('material_output')
            
        material = get_object_or_404(Material, id=m_id)
        
        try:
            # 1. Miqdorni olish va Decimalga o'tkazish
            quantity_str = request.POST.get('quantity', '0')
            quantity = Decimal(quantity_str)
            
            # Formadan kelayotgan boshqa ma'lumotlar
            recipient = request.POST.get('recipient', '').strip()
            reason = request.POST.get('reason', '').strip()
            
            # 2. Validatsiya
            if quantity <= 0:
                messages.error(request, "Chiqarish miqdori 0 dan katta bo'lishi kerak!")
                return redirect('material_output')
            
            if quantity > material.quantity:
                messages.error(request, f"Omborda yetarli emas! Bor: {material.quantity} {material.unit}")
                return redirect('material_output')

            # 3. AMALNI BAJARISH: Ombordan ayirish
            material.quantity -= quantity
            material.save()
            
            # 4. TARIXGA YOZISH: Xatolikni oldini olish uchun faqat bor maydonlarni ishlatamiz
            # DIQQAT: Agar modelingizda 'recipient' ustuni bo'lmasa, 'reason' ichiga qo'shib yuboramiz
            full_reason = f"Qabul qildi: {recipient}. Izoh: {reason}"
            
            MaterialOutput.objects.create(
                material=material,
                quantity=quantity,
                reason=full_reason, # Ko'pincha bu maydon barcha modellarda bo'ladi
                user=request.user
            )
            
            messages.success(request, f"{material.name} dan {quantity} muvaffaqiyatli chiqarildi!")
            return redirect('warehouse_dashboard')
            
        except Exception as e:
            # Agar ayirish bajarilib, tarixda xato bersa, terminalda ko'ramiz
            print(f"--- KRITIK XATO: {e} ---")
            messages.error(request, f"Tizimda xatolik: {str(e)}")
            return redirect('material_output')
    
    # GET so'rovi: materiallarni yuborish
    materials = Material.objects.filter(quantity__gt=0).order_by('name')
    return render(request, 'orders/material_output.html', {'materials': materials})
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from .models import MaterialOutput

@login_required
@staff_member_required
def output_history(request):
    """
    Chiqarish tarixi: 
    - Material va Foydalanuvchi ma'lumotlarini bitta so'rovda oladi (select_related).
    - Eng yangi amallarni ro'yxatning tepasiga chiqaradi (-created_at).
    """
    
    # Agar 'created_at' xato bersa, '-id' deb o'zgartirib ko'ring
    try:
        outputs = MaterialOutput.objects.select_related('material', 'user').order_by('-created_at')
    except:
        # Ba'zi modellarda sana 'date_output' yoki 'id' bo'lishi mumkin
        outputs = MaterialOutput.objects.select_related('material', 'user').order_by('-id')

    return render(request, 'orders/output_history.html', {'outputs': outputs})
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from django.http import HttpResponse
from .models import MaterialOutput

def export_outputs_excel(request):
    # 1. Yangi Excel kitobi yaratish
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Chiqarish Tarixi"

    # --- STILLAR ---
    # Sarlavha stili (To'q yashil fon, oq yozuv, qalin)
    header_fill = PatternFill(start_color="16A34A", end_color="16A34A", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=12)
    
    # Markazlashtirish va Hoshiya (Border)
    center_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'), 
        top=Side(style='thin'), bottom=Side(style='thin')
    )

    # 2. Sarlavha ustunlarini yozish
    columns = ['№', 'Sana', 'Vaqt', 'Material Nomi', 'Kategoriya', 'Miqdor', 'Birlik', 'Kimga / Sabab', 'Mas’ul Admin']
    ws.append(columns)

    # Sarlavha dizaynini qo'llash
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center_alignment
        cell.border = thin_border

    # 3. Ma'lumotlarni bazadan olish
    outputs = MaterialOutput.objects.select_related('material', 'user').all().order_by('-created_at')
    
    for index, output in enumerate(outputs, start=1):
        row = [
            index,
            output.created_at.strftime('%d.%m.%Y'),
            output.created_at.strftime('%H:%M'),
            output.material.name,
            output.material.category.name if output.material.category else "-",
            output.quantity,
            output.material.unit.upper(),
            output.reason if output.reason else "-",
            output.user.username
        ]
        ws.append(row)
        
        # Har bir satrga hoshiya va tekstni tekislashni qo'shish
        for cell in ws[ws.max_row]:
            cell.border = thin_border
            cell.alignment = Alignment(vertical="center", horizontal="left" if isinstance(cell.value, str) else "center")

    # 4. Ustun kengligini avtomatik sozlash (Auto-fit)
    column_widths = {
        'A': 5, 'B': 12, 'C': 10, 'D': 30, 'E': 20, 
        'F': 12, 'G': 10, 'H': 35, 'I': 15
    }
    for col, width in column_widths.items():
        ws.column_dimensions[col].width = width

    # 5. Faylni yuborish
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=chiqarish_tarixi.xlsx'
    
    wb.save(response)
    return response

import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from django.http import HttpResponse
from .models import Material

def export_inventory_excel(request):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Ombor Qoldig'i"

    # --- RANG STRIFTLARI ---
    header_fill = PatternFill(start_color="1E40AF", end_color="1E40AF", fill_type="solid") # To'q ko'k
    low_stock_fill = PatternFill(start_color="FECACA", end_color="FECACA", fill_type="solid") # Och qizil
    ok_stock_font = Font(color="059669", bold=True) # Yashil yozuv
    danger_font = Font(color="DC2626", bold=True) # Qizil yozuv
    header_font = Font(color="FFFFFF", bold=True, size=12)
    
    thin_side = Side(style='thin', color="000000")
    thin_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # 1. Sarlavha (Katta Header)
    ws.merge_cells('A1:G1')
    ws['A1'] = "OMBORXONA JORIY QOLDIQ HISOBOTI"
    ws['A1'].font = Font(bold=True, size=16, color="1E40AF")
    ws['A1'].alignment = center_align
    ws.row_dimensions[1].height = 30

    # 2. Ustun nomlari (2-qator)
    columns = ['№', 'Material Nomi', 'Kategoriya', 'Joriy Qoldiq', 'Birlik', 'Minimal Limit', 'Holat']
    ws.append(columns)

    for cell in ws[2]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center_align
        cell.border = thin_border
    ws.row_dimensions[2].height = 20

    # 3. Ma'lumotlar
    materials = Material.objects.select_related('category').all().order_by('name')
    
    for index, m in enumerate(materials, start=3):
        is_low = m.quantity <= m.min_stock_level
        status_text = "KAM QOLDI" if is_low else "YETARLI"
        
        row = [
            index - 2,
            m.name,
            m.category.name if m.category else "-",
            m.quantity,
            m.unit.upper(),
            m.min_stock_level,
            status_text
        ]
        ws.append(row)
        
        # Har bir katakni formatlash
        for cell in ws[ws.max_row]:
            cell.border = thin_border
            cell.alignment = center_align if cell.column != 2 else Alignment(horizontal="left", vertical="center", indent=1)
            
            # Agar mahsulot kam qolsa, butun qatorni och qizil qilish
            if is_low:
                cell.fill = low_stock_fill
        
        # Holat ustunini rangli qilish
        status_cell = ws.cell(row=ws.max_row, column=7)
        status_cell.font = danger_font if is_low else ok_stock_font

    # 4. Ustun kengliklarini avtomatik va aniq sozlash
    widths = [5, 40, 20, 15, 10, 15, 15]
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = width

    # 5. Faylni yuborish
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename=Ombor_Hisoboti_{timezone.now().strftime("%d_%m_%Y")}.xlsx'
    
    wb.save(response)
    return response
@login_required
@staff_member_required
def bulk_output(request):
    """Ko'p materiallarni bir vaqtda chiqarish"""
    if request.method == "POST":
        outputs_data = json.loads(request.POST.get('outputs_data', '[]'))
        
        for item in outputs_data:
            material_id = item.get('material_id')
            quantity = float(item.get('quantity', 0))
            reason = item.get('reason', '')
            
            try:
                material = Material.objects.get(id=material_id)
                if quantity <= material.quantity:
                    material.quantity -= quantity
                    material.save()
                    
                    MaterialOutput.objects.create(
                        material=material,
                        quantity=quantity,
                        reason=reason,
                        user=request.user
                    )
            except Material.DoesNotExist:
                continue
        
        messages.success(request, "Materiallar muvaffaqiyatli chiqarildi!")
        return redirect('warehouse_dashboard')
    
    materials = Material.objects.filter(quantity__gt=0).order_by('name')
    return render(request, 'orders/bulk_output.html', {'materials': materials})
@staff_member_required
@login_required
def order_picking_list(request, order_id):
    """
    Buyurtma uchun 'Sarflash ro'yxati' (Picking List)
    Bu funksiya ombordagi materiallar yetarli yoki yo'qligini tekshiradi.
    """
    order = get_object_or_404(Order, id=order_id)
    
    # 1. Kalkulyatordan hisob-kitoblarni olamiz
    calc_data = order.calculate_materials()
    
    if not calc_data:
        return render(request, 'orders/picking_list.html', {
            'order': order,
            'error': "Ushbu buyurtma turi uchun hisob-kitob mantiqi topilmadi."
        })

    # 2. Ombor bilan solishtirish (Report tayyorlash)
    report = []
    can_produce = True
    
    # Materiallar xaritasi: Kalkulyatordagi kalit so'z -> Bazadagi qidiriladigan nom
    check_list = [
        {'key': 'foam_volume', 'search': 'siryo', 'label': 'Siryo (Foam)'},
        {'key': 'sheets_area', 'search': 'list', 'label': 'List (Metal)'},
    ]

    for item in check_list:
        needed = calc_data.get(item['key'], 0)
        # Bazadan nomiga qarab qidiramiz
        material = Material.objects.filter(name__icontains=item['search']).first()
        
        available = material.quantity if material else 0
        is_enough = available >= needed if material else False
        
        if not is_enough:
            can_produce = False
            
        report.append({
            'label': item['label'],
            'needed': needed,
            'available': available,
            'status': is_enough,
            'unit': material.unit if material else '?'
        })

    context = {
        'order': order,
        'report': report,
        'can_produce': can_produce,
        'calc_data': calc_data
    }
    return render(request, 'orders/picking_list.html', context)
from django.shortcuts import render, redirect
from .models import Material, Category

@login_required
def add_material(request):
    if request.method == "POST":
        name = request.POST.get('name').strip() # Nomdagi bo'shliqlarni olib tashlaymiz
        category_name = request.POST.get('category_name')
        quantity = float(request.POST.get('quantity', 0))
        unit = request.POST.get('unit')
        min_stock = request.POST.get('min_stock', 0)

        # Kategoriya mantiqi
        category_obj = None
        if category_name:
            category_obj, created = Category.objects.get_or_create(name=category_name.strip())

        # MAHSULOTNI TEKSHIRISH VA SAQLASH
        # Nomiga qarab bazadan qidiramiz
        material, created = Material.objects.get_or_create(
            name=name,
            defaults={
                'category': category_obj,
                'quantity': quantity,
                'unit': unit,
                'min_stock_level': min_stock
            }
        )

        # Agar mahsulot allaqachon bor bo'lsa (created=False), shunchaki miqdorini qo'shamiz
        if not created:
            material.quantity = float(material.quantity) + quantity
            material.save()

        # Redirect qilishda xato bermasligi uchun loyiha nomini tekshiring
        try:
            return redirect('warehouse_dashboard')
        except:
            return redirect('warehouse_dashboard')

    categories = Category.objects.all()
    return render(request, 'orders/add_material.html', {'categories': categories})


@login_required
def guard_dashboard(request):
    user_lower = request.user.username.lower()
    if not ("qorovul" in user_lower or "guard" in user_lower or request.user.is_superuser):
        return HttpResponseForbidden("Sizda qorovul paneliga kirish huquqi yo'q!")

    if request.method == 'POST':
        order_id = request.POST.get('order_id')
        action = request.POST.get('action')
        order = get_object_or_404(Order, id=order_id)
        img = request.FILES.get('guard_img')
        now = timezone.now()

        if action == 'enter':
            status_text = "KIRDI (Yukxonaga)"
            status_emoji = "📥"
            # KIRISH VAQTINI MUHRLASH
            order.work_started_at = now 
            order.save()
        elif action == 'exit':
            status_text = "CHIQDI (Zavoddan)"
            status_emoji = "📤"
            # CHIQISH VAQTINI MUHRLASH VA STATUSNI O'ZGARTIRISH
            order.status = 'YUK_CHIQDI'
            order.work_finished_at = now
            order.save()

        # Telegramga yuborish (vaqt bilan)
        if img:
            caption = (
                f"🛡️ #QOROVUL_NAZORATI\n"
                f"{status_emoji} {status_text}\n"
                f"📦 Buyurtma: #{order.id}\n"
                f"🚛 Moshina: {order.worker_comment}\n"
                f"🕒 Vaqt: {now.strftime('%H:%M')}"
            )
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
                img.seek(0)
                files = {'photo': (img.name, img.read(), img.content_type)}
                requests.post(url, data={'chat_id': TELEGRAM_GROUP_ID, 'caption': caption}, files=files)
            except Exception as e:
                print(f"Telegram error: {e}")

        messages.success(request, f"#{order.id} {status_text} tasdiqlandi (Vaqt: {now.strftime('%H:%M')}).")
        return redirect('guard_dashboard')
    


    # 1. KUTILAYOTGANLAR (Hali chiqmagan moshinalar)
    pending_orders = Order.objects.filter(
        status='BAJARILDI'
    ).exclude(
        Q(worker_comment="") | Q(worker_comment__isnull=True)
    ).order_by('-work_finished_at')

    # 2. BUGUNGI TARIX (Kirgan va chiqqan moshinalar jadvali)
    today = timezone.now().date()
    today_history = Order.objects.filter(
        Q(work_started_at__date=today) | Q(work_finished_at__date=today)
    ).filter(
        status__in=['BAJARILDI', 'YUK_CHIQDI']
    ).order_by('-id')

    context = {
        'orders': pending_orders,
        'history': today_history,
    }
    return render(request, 'orders/guard_dashboard.html', context)










from datetime import datetime  # BU MUHIM: importni shunday o'zgartiring
import requests  
from django.utils import timezone
from django.shortcuts import render, redirect
from django.contrib import messages
from .models import GuardPatrol

# Telegram bot sozlamalari
BOT_TOKEN = '8559719741:AAGa5BnxXt2rxjC-gKFnzboBiJQgPUY2GzU' # O'zingizniki bilan almashtiring
CHAT_ID = '-1002338157363' # Guruh ID sini qo'ying
import json
import requests
from datetime import datetime
from django.utils import timezone
from django.shortcuts import render, redirect
from django.contrib import messages
from .models import GuardPatrol

# Sozlamalarni o'zgaruvchilarga chiqaramiz
BOT_TOKEN = "8559719741:AAGa5BnxXt2rxjC-gKFnzboBiJQgPUY2GzU"
CHAT_ID = "-1003274223599"

import json
import requests
from datetime import datetime
from django.utils import timezone
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .models import GuardPatrol

# --- TELEGRAM SOZLAMALARI ---
BOT_TOKEN = "8559719741:AAGa5BnxXt2rxjC-gKFnzboBiJQgPUY2GzU"
CHAT_ID = "-1002338157363"

import json
import requests

def send_patrol_to_telegram(patrol):
    """Hisobotni va rasmlarni bitta albom qilib Telegramga yuborish"""
    map_url = f"https://www.google.com/maps?q={patrol.latitude},{patrol.longitude}"

    caption = (
        f"🚨 *YANGI PATRUL HISOBOTI*\n\n"
        f"👤 *Qorovul:* {patrol.guard.get_full_name() or patrol.guard.username}\n"
        f"⏰ *Vaqt:* {patrol.patrol_time_slot}\n"
        f"📅 *Sana:* {patrol.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"📍 [Xaritada ko'rish]({map_url})"
    )

    media_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMediaGroup"
    files = {}
    media = []

    # ✅ 4 ta rasm
    image_fields = [patrol.image1, patrol.image2, patrol.image3, patrol.image4]

    for i, img_field in enumerate(image_fields, 1):
        if img_field and img_field.name:
            file_key = f"pic{i}"
            try:
                # local storage bo‘lsa path bo‘ladi
                files[file_key] = open(img_field.path, "rb")
                media.append({
                    "type": "photo",
                    "media": f"attach://{file_key}",
                    "caption": caption if i == 1 else "",
                    "parse_mode": "Markdown",
                })
            except Exception as e:
                print(f"Rasm ochishda xato ({file_key}): {e}")

    if not media:
        print("Telegramga yuborish bekor: rasm topilmadi (media bo'sh).")
        return None

    try:
        response = requests.post(
            media_url,
            data={"chat_id": CHAT_ID, "media": json.dumps(media)},
            files=files,
            timeout=40
        )

        # ✅ debug: aynan nima xato ekanini ko‘rasan
        print("TELEGRAM STATUS =", response.status_code)
        print("TELEGRAM TEXT =", response.text)

        return response.json()

    except requests.exceptions.Timeout:
        print("Telegram xatosi: Timeout (rasmlar katta bo'lishi mumkin).")
    except Exception as e:
        print(f"Telegram yuborishda kutilmagan xato: {e}")
    finally:
        for f in files.values():
            try:
                f.close()
            except:
                pass

    return None

            
from datetime import datetime
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from .models import GuardPatrol  # sizda qaysi appda bo'lsa shu yo'lni to'g'rilang


@login_required
def guard_patrol_view(request):
    if not (request.user.is_staff or request.user.username == 'Qorovul'):
        messages.error(request, "Sizda bu sahifaga kirish ruxsati yo'q!")
        return redirect('home')

    now_dt = timezone.localtime()
    now_time = now_dt.time()
    today = now_dt.date()

    # ✅ Patrul vaqtlar jadvali (to'g'ri formatda)
    patrol_slots = [
        ("02:00", "02:20"),
        ("04:00", "04:20"),
        ("05:00", "05:20"),
        ("12:00", "12:20"),  # ⚠️ sizda 12:30-12:20 xato edi
        ("14:00", "14:20"),
        ("18:00", "18:20"),
        ("22:00", "22:20"),
    ]

    # Bugun topshirilgan slotlarni 1 martada olib olamiz (tez)
    completed_qs = GuardPatrol.objects.filter(
        guard=request.user,
        created_at__date=today
    ).values_list('patrol_time_slot', flat=True)
    completed_set = set(completed_qs)

    # Hozir active slotni topamiz + hamma slotlar uchun status tayyorlaymiz
    active_slot = None
    slots_ui = []

    for start, end in patrol_slots:
        start_t = datetime.strptime(start, "%H:%M").time()
        end_t = datetime.strptime(end, "%H:%M").time()

        slot_label = f"{start} - {end}"
        is_active = (start_t <= now_time <= end_t)

        if is_active and active_slot is None:
            active_slot = slot_label

        slots_ui.append({
            "label": slot_label,
            "start": start,
            "end": end,
            "is_active": is_active,
            "is_completed": slot_label in completed_set,
        })

    # POST faqat active slot bo'lsa ishlasin
    if request.method == "POST":
        slot_from_post = request.POST.get("slot")  # qaysi slotdan yuborildi

        if not active_slot or slot_from_post != active_slot:
            messages.error(request, "Hozir patrul vaqti emas yoki noto‘g‘ri vaqt tanlandi!")
            return redirect('guard_patrol')

        if active_slot in completed_set:
            messages.warning(request, "Siz bu vaqt oralig'i uchun hisobot topshirib bo'lgansiz!")
            return redirect('guard_patrol')

        img1 = request.FILES.get('img1')
        img2 = request.FILES.get('img2')
        img3 = request.FILES.get('img3')
        img4 = request.FILES.get('img4')
        lat = request.POST.get('lat')
        lng = request.POST.get('lng')

        if img1 and img2 and img3 and img4:
            patrol = GuardPatrol.objects.create(
                guard=request.user,
                checkpoint_name="Umumiy nazorat",
                patrol_time_slot=active_slot,
                image1=img1, image2=img2, image3=img3, image4=img4,   # ✅ shu qo‘shildi
                latitude=float(lat) if lat and lat != "undefined" else 0.0,
                longitude=float(lng) if lng and lng != "undefined" else 0.0
            )

            result = send_patrol_to_telegram(patrol)  # ✅ resultni ushlab qolamiz
            print("TELEGRAM RESULT =", result)         # ✅ konsolda ko‘rasan

            messages.success(request, "Patrul hisoboti muvaffaqiyatli topshirildi!")
            return redirect('guard_patrol')
        else:
            messages.error(request, "Xatolik: 4 ta rasm yuklash majburiy!")


         

    return render(request, 'orders/patrol.html', {
        "slots": slots_ui,
        "active_slot": active_slot,
        "current_time": now_dt,  # template'da vaqt ko'rsatish uchun
    })



def rankings_view(request):
    """Ustalar reytingi sahifasi"""
    # models.Q o'rniga Q o'zi ishlatildi (import qismiga qarang)
    workers_list = Worker.objects.annotate(
        total_finished=Count('orders', filter=Q(orders__status='TUGATILDI')),
        total_kvadrat=Sum('orders__panel_kvadrat', filter=Q(orders__status='TUGATILDI'))
    ).order_by('-total_kvadrat')

    context = {
        'workers': workers_list,
    }
    return render(request, 'orders/rankings.html', context)
# ----------------------------------------------------------------------
# BUYURTMA TAHSILOTLARI
# ----------------------------------------------------------------------
import requests
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.http import JsonResponse
from .models import Order
from .forms import OrderForm

# TELEGRAM BOT CONFIG
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from .models import Order
from .forms import OrderForm
import requests
from django.utils import timezone
from django.contrib.admin.models import LogEntry, CHANGE
from django.contrib.contenttypes.models import ContentType

# TELEGRAM CONFIG (Siz so'ragandek shu yerda qoldi)
TELEGRAM_BOT_TOKEN = "8559719741:AAGa5BnxXt2rxjC-gKFnzboBiJQgPUY2GzU"
TELEGRAM_GROUP_ID = "-1002338157363"

def is_in_group(user, group_name):
    return user.groups.filter(name=group_name).exists()

@login_required
def order_detail(request, pk):
    order = get_object_or_404(Order, pk=pk)

    # 1. Ruxsatlarni aniqlash (O'zgarishsiz)
    is_glavniy_admin = request.user.is_superuser or is_in_group(request.user, 'Glavniy Admin')
    is_manager = is_in_group(request.user, 'Menejer/Tasdiqlovchi')
    is_production_boss = is_in_group(request.user, "Ishlab Chiqarish Boshlig'i")
    is_worker = is_in_group(request.user, 'Usta') 
    is_observer_user = is_in_group(request.user, 'Kuzatuvchi')

    # Kuzatuvchi uchun faqat ko'rish rejimi
    if is_observer_user:
        return render(request, 'orders/order_detail.html', {'order': order, 'readonly': True})

    # 2. Usta ruxsatini tekshirish
    is_assigned_worker = False
    if is_worker:
        try:
            worker_profile = request.user.worker_profile
            is_assigned_worker = order.assigned_workers.filter(pk=worker_profile.pk).exists()
        except Exception:
            is_assigned_worker = False

    if is_worker and not is_assigned_worker and not is_production_boss and not is_glavniy_admin:
        messages.error(request, "Siz faqat o'zingizga tayinlangan buyurtmalarni ko'rishingiz mumkin.")
        return redirect('order_list')

    # 3. Formani boshqarish (Admin/Manager uchun)
    order_form = None
    if is_glavniy_admin or is_manager or is_production_boss:
        order_form = OrderForm(request.POST or None, request.FILES or None, instance=order)
        if request.method == 'POST' and 'upload_type' not in request.POST:
            if order_form.is_valid():
                order_form.save()
                messages.success(request, "Buyurtma muvaffaqiyatli yangilandi.")
                return redirect('order_detail', pk=order.pk)

    # 4. POST: Rasm yuklash va Telegram (TO'G'RILANGAN QISM)
    # Ruxsat: Agar usta biriktirilgan bo'lsa YOKI admin/boshliq bo'lsa rasm yuklay oladi
    can_upload = is_assigned_worker or is_glavniy_admin or is_production_boss

    if request.method == 'POST' and 'upload_type' in request.POST and can_upload:
        upload_type = request.POST.get('upload_type')
        image = request.FILES.get(upload_type)

        if image and upload_type in ['start_image', 'finish_image']:
            # Emojilarsiz xavfsiz matn
            status_text = "ISH BOSHLANDI" if upload_type == 'start_image' else "ISH YAKUNLANDI"
            debt = order.total_price - order.prepayment
            payment_info = "Toliq tolangan" if debt <= 0 else f"Qarz: {debt} USD"
            customer_name = order.customer.name if hasattr(order, 'customer') and order.customer else "Nomalum"

            caption = (
                f"{status_text}\n"
                f"-------------------------------\n"
                f"Buyurtma: #{order.id}\n"
                f"Mijoz: {order.customer_unique_id} / {customer_name}\n"
                f"Mahsulot: {order.product_name or 'Aniqlanmagan'}\n"
                f"Olcham: {order.panel_thickness or '0'} sm | {order.panel_kvadrat or '0'} m2\n"
                f"Jami: {order.total_price} USD\n"
                f"Holat: {payment_info}\n"
                f"-------------------------------\n"
                f"Masul: {request.user.get_full_name() or request.user.username}\n"
                f"Vaqt: {timezone.now().strftime('%d.%m.%Y %H:%M')}"
            )

            try:
                # Telegramga yuborish
                response = requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                    data={
                        "chat_id": TELEGRAM_GROUP_ID,
                        "caption": caption
                    },
                    files={"photo": image},
                    timeout=20
                )

                if response.status_code == 200:
                    # Telegramga muvaffaqiyatli ketgandagina bazani yangilaymiz
                    if upload_type == 'start_image':
                        order.start_image = image
                        order.start_confirmed = True
                        order.started_by = request.user
                        order.work_started_at = timezone.now()
                        order.status = 'USTA_QABUL_QILDI'
                    else:
                        order.finish_image = image
                        order.finish_confirmed = True
                        order.finished_by = request.user
                        order.work_finished_at = timezone.now()
                        order.status = 'USTA_TUGATDI'

                    order.save()

                    LogEntry.objects.create(
                        user_id=request.user.id,
                        content_type_id=ContentType.objects.get_for_model(order).pk,
                        object_id=order.pk,
                        object_repr=str(order),
                        action_flag=CHANGE,
                        change_message=f"Telegramga hisobot yuborildi: {status_text}"
                    )
                    messages.success(request, "Hisobot Telegramga yuborildi va saqlandi.")
                else:
                    messages.error(request, f"Telegram bot rad etdi: {response.text}")

            except Exception as e:
                messages.error(request, f"Aloqa xatoligi: {str(e)}")
            
            return redirect('order_detail', pk=order.pk)
        else:
            messages.error(request, "Rasm tanlanmagan!")

    # 5. Sahifani yuklash uchun context
    context = {
        'order': order,
        'order_form': order_form,
        'is_worker': is_worker,
        'is_assigned_worker': is_assigned_worker,
        'is_privileged': is_glavniy_admin or is_manager or is_production_boss,
    }
    return render(request, 'orders/order_detail.html', context)

# ----------------------------------------------------------------------
# USTA HARAKATLARI FUNKSIYALARI
# ----------------------------------------------------------------------
@login_required
@user_passes_test(lambda u: is_in_group(u, 'Usta') or u.is_superuser, login_url='/login/')
def order_worker_accept(request, pk):
    """Usta buyurtmani qabul qilish."""
    # Kuzatuvchi tekshiruvi
    if is_observer(request.user):
        messages.error(request, "Kuzatuvchi rejimida bu amalni bajarish mumkin emas.")
        return redirect('order_list')
        
    order = get_object_or_404(Order, pk=pk)
    
    if not request.user.is_superuser and not order.assigned_workers.filter(user=request.user).exists():
        messages.error(request, "Siz bu buyurtmaga tayinlanmagansiz.")
        return redirect('order_list')

    if order.status == 'TASDIQLANDI':
        if not order.start_image:
             messages.error(request, "Ishni qabul qilishdan oldin, **Boshlanish Rasmini** yuklashingiz kerak.")
             return redirect('order_detail', pk=order.pk)

        order.status = 'USTA_QABUL_QILDI'
        order.save(update_fields=['status'])
        messages.success(request, f"Buyurtma #{order.order_number} ustalar tomonidan qabul qilindi. Endi 'Boshlash' tugmasini bosing.")
    else:
        messages.warning(request, f"Buyurtma #{order.order_number} faqat 'Tasdiqlandi' statusida qabul qilinishi mumkin.")
        
    return redirect('order_list')

@login_required
@user_passes_test(lambda u: is_in_group(u, 'Usta') or u.is_superuser, login_url='/login/')
def order_worker_start(request, pk):
    """Usta ishni boshlash."""
    # Kuzatuvchi tekshiruvi
    if is_observer(request.user):
        messages.error(request, "Kuzatuvchi rejimida bu amalni bajarish mumkin emas.")
        return redirect('order_list')
        
    order = get_object_or_404(Order, pk=pk)
    
    if not request.user.is_superuser and not order.assigned_workers.filter(user=request.user).exists():
        messages.error(request, "Siz bu operatsiyani bajarishga ruxsat etilmagansiz.")
        return redirect('order_list')

    if order.status == 'USTA_QABUL_QILDI':
        order.status = 'USTA_BOSHLA'
        order.worker_started_at = timezone.now() 
        order.save(update_fields=['status', 'worker_started_at'])
        messages.success(request, f"Buyurtma #{order.order_number} bo'yicha ish boshlandi. Status: USTA BOSHLADI.")
    else:
        messages.warning(request, f"Ishni boshlash uchun buyurtma 'Usta Qabul Qildi' statusida bo'lishi kerak.")
        
    return redirect('order_list')


@login_required
@user_passes_test(lambda u: is_in_group(u, 'Usta') or u.is_superuser, login_url='/login/')
def order_worker_finish(request, pk):
    """Usta ishni yakunlash va avtomatik ravishda keyingi bosqich ustalari uchun buyurtma ochish."""
    
    # 1. Kuzatuvchi (Observer) tekshiruvi
    if is_observer(request.user):
        messages.error(request, "Kuzatuvchi rejimida bu amalni bajarish mumkin emas.")
        return redirect('order_list')
        
    order = get_object_or_404(Order, pk=pk)

    # 2. Huquqlarni tekshirish (faqat biriktirilgan usta yoki superuser)
    if not request.user.is_superuser and not order.assigned_workers.filter(user=request.user).exists():
        messages.error(request, "Siz bu operatsiyani bajarishga ruxsat etilmagansiz.")
        return redirect('order_list')

    # 3. Rasm yuklanganligini tekshirish
    if not order.finish_image:
         messages.error(request, "Ishni tugatishdan oldin, yakuniy rasm (Tugatish Rasmi) yuklashingiz kerak.")
         return redirect('order_detail', pk=order.pk)
        
    # 4. Statusni yangilash
    if order.status in ['USTA_BOSHLA', 'ISHDA']: 
        current_time = timezone.now()
        order.status = 'USTA_TUGATDI'
        order.worker_finished_at = current_time 
        
        # Muddatdan o'tib ketgan bo'lsa ogohlantirish
        if order.deadline and current_time > order.deadline:
            # Agar funksiya mavjud bo'lsa chaqiriladi
            if 'check_and_create_overdue_alerts' in globals():
                check_and_create_overdue_alerts(order)
            messages.warning(request, f"⚠️ Buyurtma #{order.order_number} muddatidan kech yakunlandi.")
            
        order.save(update_fields=['status', 'worker_finished_at'])

        # ================================================================
        # YANGI ZANJIRSIMON ALGORITM (List usta -> Panel/Ugol usta)
        # ================================================================
        
        # Agar hozirgi foydalanuvchi "List usta" guruhida bo'lsa
        if is_in_group(request.user, "List usta"):
            # Keyingi bosqich ustalari (Panel va Ugol) guruhlarini bazadan topamiz
            next_workers = Worker.objects.filter(
                Q(user__groups__name="Panel usta") | Q(user__groups__name="Ugol usta")
            )
            
            if next_workers.exists():
                # Yangi order raqami (masalan: ORD-100 bo'lsa, ORD-100-PU bo'ladi)
                new_order_number = f"{order.order_number}-PU"
                
                # Agar bunaqa raqamli buyurtma hali ochilmagan bo'lsa (dublikat bo'lmasligi uchun)
                if not Order.objects.filter(order_number=new_order_number).exists():
                    new_order = Order.objects.create(
                        order_number=new_order_number,
                        material=order.material,
                        quantity=order.quantity,
                        drawings_pdf=order.drawings_pdf, # List usta ishlatgan chizmani o'tkazamiz
                        status='TASDIQLANDI',           # Avtomatik tasdiqlangan holatda
                        created_by=order.created_by,
                        deadline=timezone.now() + timedelta(days=1), # 1 kun muddat
                        notes=f"List usta #{order.order_number} ishini yakunlagani uchun avtomatik yaratildi."
                    )
                    
                    # Topilgan barcha Panel va Ugol ustalarni yangi buyurtmaga biriktiramiz
                    for worker in next_workers:
                        new_order.assigned_workers.add(worker)
                        
                        # Har biriga bildirishnoma yuboramiz
                        Notification.objects.create(
                            user=worker.user,
                            order=new_order,
                            message=f"Yangi ish: List usta #{order.order_number} chizmasini bitirdi. Panel/Ugol bosqichini boshlang."
                        )
                    
                    messages.success(request, "Panel va Ugol ustalari uchun avtomatik buyurtma yaratildi.")
        # ================================================================

        messages.success(request, f"Buyurtma #{order.order_number} yakunlandi.")
    else:
        messages.warning(request, "Ishni yakunlash uchun buyurtma 'Usta Boshladi' yoki 'Ishda' statusida bo'lishi kerak.")
        
    return redirect('order_list')

# ----------------------------------------------------------------------
# YANGI: USTALAR PANELI FUNKSIYALARI
# ----------------------------------------------------------------------
@login_required
@user_passes_test(lambda u: is_in_group(u, 'Usta') or u.is_superuser or 
                   is_in_group(u, "Ishlab Chiqarish Boshlig'i") or 
                   is_in_group(u, 'Kuzatuvchi'), login_url='/login/')  # ✅ YANGI
def worker_panel(request):
    """
    Ustalar paneli - barcha ustalar ro'yxati
    """
    # Faqat admin, ishlab chiqarish boshliqlari va kuzatuvchilar ko'ra oladi
    is_glavniy_admin = request.user.is_superuser or is_in_group(request.user, 'Glavniy Admin')
    is_production_boss = is_in_group(request.user, "Ishlab Chiqarish Boshlig'i")
    is_observer = is_in_group(request.user, 'Kuzatuvchi')  # ✅ YANGI
    
    if not (is_glavniy_admin or is_production_boss or is_observer):  # ✅ YANGI
        messages.error(request, "Sizda bu sahifani ko'rish uchun ruxsat yo'q.")
        return redirect('order_list')
    
    # Barcha ustalarni olish
    workers = Worker.objects.all().select_related('user').annotate(
        completed_orders_count=Count(
            'orders', 
            filter=Q(orders__status__in=['TAYYOR', 'BAJARILDI'])
        ),
        total_kvadrat=Sum(
            'orders__panel_kvadrat', 
            filter=Q(orders__status__in=['TAYYOR', 'BAJARILDI'])
        )
    )

    context = {
        'workers': workers,
        'is_glavniy_admin': is_glavniy_admin,
        'is_production_boss': is_production_boss,
        'is_observer': is_observer,
    }
    
    return render(request, 'orders/worker_panel.html', context)

@login_required
@user_passes_test(lambda u: is_in_group(u, 'Usta') or u.is_superuser or 
                   is_in_group(u, "Ishlab Chiqarish Boshlig'i") or 
                   is_in_group(u, 'Kuzatuvchi'), login_url='/login/')  # ✅ YANGI
def worker_orders(request, worker_id):
    """
    Muayyan ustaning barcha buyurtmalari
    """
    worker = get_object_or_404(Worker, id=worker_id)
    
    # Ruxsatni tekshirish
    is_glavniy_admin = request.user.is_superuser or is_in_group(request.user, 'Glavniy Admin')
    is_production_boss = is_in_group(request.user, "Ishlab Chiqarish Boshlig'i")
    is_worker_self = request.user == worker.user
    is_observer = is_in_group(request.user, 'Kuzatuvchi')  # ✅ YANGI
    
    if not (is_glavniy_admin or is_production_boss or is_worker_self or is_observer):  # ✅ YANGI
        messages.error(request, "Sizda bu sahifani ko'rish uchun ruxsat yo'q.")
        return redirect('order_list')
    
    # Filtrlash parametrlari
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    status_filter = request.GET.get('status', '')
    
    # Ustaning buyurtmalari
    # orders = Order.objects.filter(assigned_workers=worker).order_by('-created_at')
    # select_related - bog'langan model ma'lumotlarini bitta so'rovda oladi
# prefetch_related - ManyToMany (ustalar) bog'liqligini tezlashtiradi
    orders = Order.objects.filter(assigned_workers=worker)\
        .select_related('parent_order')\
        .prefetch_related('assigned_workers')\
        .order_by('-created_at')
    
    # Filtrlash
    if start_date:
        orders = orders.filter(created_at__gte=start_date)
    if end_date:
        end_datetime = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
        orders = orders.filter(created_at__lt=end_datetime)
    if status_filter:
        orders = orders.filter(status=status_filter)
    
    # Statistikani hisoblash
    total_orders = orders.count()
    completed_orders = orders.filter(status__in=['TAYYOR', 'BAJARILDI']).count()
    total_kvadrat = orders.filter(status__in=['TAYYOR', 'BAJARILDI']).aggregate(
        Sum('panel_kvadrat')
    )['panel_kvadrat__sum'] or 0
    
    context = {
        'worker': worker,
        'orders': orders,
        'total_orders': total_orders,
        'completed_orders': completed_orders,
        'total_kvadrat': total_kvadrat,
        'start_date': start_date,
        'end_date': end_date,
        'status_filter': status_filter,
        'is_glavniy_admin': is_glavniy_admin,
        'is_production_boss': is_production_boss,
        'is_worker_self': is_worker_self,
        'is_observer': is_observer,  # ✅ YANGI
    }
    
    return render(request, 'orders/worker_orders.html', context)

# ----------------------------------------------------------------------
# QOLGAN FUNKSIYALAR
# views.py - order_create funksiyasini yangilang
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from .models import Order, Customer, User # Kerakli modellarni import qiling
from .forms import OrderForm

from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from .models import Order, Customer, User
from .forms import OrderForm
@login_required
@user_passes_test(
    lambda u: u.is_superuser or is_in_group(u, 'Glavniy Admin') or is_in_group(u, 'Manager'),
    login_url='/login/'
)
def order_create(request):
    """Buyurtma yaratish - Yangi va mavjud IDlar bilan ishlash"""
    
    # 1. Barcha manbalardan ID va ismlarni olish (Bo'sh joylarni tozalab)
    db_customers = {str(c.unique_id).strip(): c.name for c in Customer.objects.all()}
    order_ids = Order.objects.values_list('customer_unique_id', flat=True).distinct()
    
    all_unique_ids = set(list(db_customers.keys()) + [str(uid).strip() for uid in order_ids if uid])
    
    # Select2 uchun ma'lumotlar ro'yxati
    customers_data = []
    for uid in all_unique_ids:
        name = db_customers.get(uid)
        display_text = f"{uid} - {name}" if name else f"{uid}"
        customers_data.append({
            'id': uid,
            'text': display_text
        })

    if request.method == 'POST':
        form = OrderForm(request.POST, request.FILES)
        
        # MUHIM: Select2 dan kelayotgan IDni to'g'ridan-to'g'ri POST'dan olamiz
        # Chunki form.is_valid() yangi IDni "yaroqsiz" deb topishi mumkin
        customer_unique_id = request.POST.get('customer_unique_id', '').strip()
        customer_name = request.POST.get('customer_name', '').strip()
        customer_phone = request.POST.get('customer_phone', '').strip() or None

        if form.is_valid():
            try:
                order = form.save(commit=False)
                order.created_by = request.user
                
                if not customer_unique_id:
                    messages.error(request, "❌ Iltimos, mijoz uchun unikal raqam kiriting.")
                else:
                    # 2. Mijozni bazadan qidirish yoki yangi yaratish
                    # update_or_create emas, get_or_create ishlatamiz (ism o'zgarmasligi uchun)
                    customer, created = Customer.objects.get_or_create(
                        unique_id=customer_unique_id,
                        defaults={'name': customer_name, 'phone': customer_phone}
                    )
                    
                    # Agar mijoz bazada bo'lsa-yu, lekin ismi yo'q bo'lsa (Noma'lum bo'lsa), yangilash
                    if not created and customer_name and (not customer.name or customer.name == "Noma'lum mijoz"):
                        customer.name = customer_name
                        customer.save()

                    order.customer = customer
                    order.customer_unique_id = customer_unique_id
                    order.status = 'KIRITILDI'

                    # 3. Ish turi logikasi (Panel usta tayinlash)
                    worker_type = form.cleaned_data.get('worker_type', 'LIST')
                    if worker_type == 'ESHIK':
                        eshik_turi = form.cleaned_data.get('eshik_turi', '')
                        zamokli_eshik = form.cleaned_data.get('zamokli_eshik', False)
                        if eshik_turi and '(' not in str(eshik_turi):
                            zamok_status = "Zamokli" if zamokli_eshik else "Zamoksiz"
                            order.eshik_turi = f"{eshik_turi} ({zamok_status})"

                    if worker_type == 'PANEL':
                        try:
                            u1 = User.objects.get(username='panel_usta')
                            u2 = User.objects.get(username='panel_usta2')
                            last_panel = Order.objects.filter(worker_type='PANEL').order_by('-id').first()
                            order.assigned_to = u2 if last_panel and last_panel.id % 2 != 0 else u1
                        except User.DoesNotExist:
                            pass 

                    # 4. Saqlash
                    order.save()
                    form.save_m2m()
                    
                    # Bildirishnoma yuborish
                    try:
                        from .utils import send_notifications_for_new_order 
                        send_notifications_for_new_order(order)
                    except Exception:
                        pass

                    messages.success(request, f"✅ Buyurtma №{order.order_number} kiritildi!")
                    return redirect('order_list')
                
            except Exception as e:
                messages.error(request, f"❌ Tizim xatoligi: {str(e)}")
    else:
        form = OrderForm()

    context = {
        'form': form, 
        'title': 'Yangi Buyurtma Kiritish',
        'customers_list': customers_data,
        'existing_customer_ids': list(all_unique_ids)
    }
    return render(request, 'orders/order_create.html', context)

@login_required
def order_edit(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if request.method == 'POST':
        form = OrderForm(request.POST, request.FILES, instance=order)
        if form.is_valid():
            form.save()
            messages.success(request, f"#{order.customer_unique_id} buyurtma muvaffaqiyatli yangilandi.")
            return redirect('order_detail', pk=order.pk)
    else:
        form = OrderForm(instance=order)
        # Faqat kerakli ustalarni filtrlab ko'rsatish
        form.fields['assigned_workers'].queryset = Worker.objects.filter(
            Q(user__groups__name="List usta") | Q(user__groups__name="Eshik usta")
        ).distinct()
        
    # Fayl nomi order_edit.html ga o'zgardi
    return render(request, 'orders/order_edit.html', {'form': form, 'is_edit': True})


from django.contrib.admin.models import LogEntry, CHANGE
from django.contrib.contenttypes.models import ContentType

@login_required
def order_confirm(request, pk):
    """2-Bosqich: Buyurtmani tasdiqlash."""
    
    # 1. Kuzatuvchi tekshiruvi
    if is_observer(request.user):
        messages.error(request, "Kuzatuvchi rejimida bu amalni bajarish mumkin emas.")
        return redirect('order_list')
        
    order = get_object_or_404(Order, pk=pk)
    
    # 2. Ruxsatlarni tekshirish (Admin yoki Menejer bo'lishi shart)
    is_privileged = request.user.is_superuser or is_in_group(request.user, 'Menejer/Tasdiqlovchi')
    
    if not is_privileged: 
        messages.error(request, "Sizda bu buyurtmani tasdiqlash uchun ruxsat yo'q.")
        return redirect('order_list')

    # 3. Statusni yangilash
    if order.status == 'KIRITILDI':
        order.status = 'TASDIQLANDI'
        order.save()
        
        # LogEntry xatosini tuzatish (log_action o'rniga create)
        LogEntry.objects.create(
            user_id=request.user.id,
            content_type_id=ContentType.objects.get_for_model(order).pk,
            object_id=order.pk,
            object_repr=str(order),
            action_flag=CHANGE,
            change_message=f"Status o'zgartirildi: KIRITILDI -> TASDIQLANDI"
        )
        
        messages.success(request, f"Buyurtma №{order.order_number} Tasdiqlandi.")
        
        # 4. Bildirishnomalar yuborish
        # Buyurtma yaratuvchisiga
        if order.created_by:
            Notification.objects.create(
                user=order.created_by,
                order=order,
                message=f"Siz kiritgan buyurtma №{order.order_number} Muvaffaqiyatli Tasdiqlandi."
            )
        
        # Ishlab chiqarish boshlig'iga
        try:
            boss_group = Group.objects.get(name="Ishlab Chiqarish Boshlig'i")
            for boss in boss_group.user_set.all():
                Notification.objects.create(
                    user=boss,
                    order=order,
                    message=f"Yangi buyurtma №{order.order_number} Tasdiqlandi. Ishlab chiqarishni boshlashingiz mumkin."
                )
        except Group.DoesNotExist:
            pass # Xabar berish shart emas bo'lsa

        # Tayinlangan ustalarga
        if order.assigned_workers.exists():
            for worker in order.assigned_workers.all():
                Notification.objects.create(
                    user=worker.user,
                    order=order,
                    message=f"Tayinlangan buyurtma №{order.order_number} Tasdiqlandi! Ishni boshlashingiz mumkin."
                )
            
    else:
        messages.warning(request, "Bu buyurtma allaqachon tasdiqlangan yoki boshqa bosqichda.")
        
    return redirect('order_list')
@login_required
def order_reject(request, pk):
    """Buyurtmani Rad Etish."""
    # Kuzatuvchi tekshiruvi
    if is_observer(request.user):
        messages.error(request, "Kuzatuvchi rejimida bu amalni bajarish mumkin emas.")
        return redirect('order_list')
        
    order = get_object_or_404(Order, pk=pk)
    
    if not is_in_group(request.user, 'Menejer/Tasdiqlovchi'): 
        messages.error(request, "Sizda bu buyurtmani rad etish uchun ruxsat yo'q.")
        return redirect('order_list')

    if order.status == 'KIRITILDI':
        order.status = 'RAD_ETILDI'
        order.save()
        
        LogEntry.objects.log_action(
            user_id=request.user.id,
            content_type_id=ContentType.objects.get_for_model(order).pk,
            object_id=order.pk,
            object_repr=str(order),
            action_flag=CHANGE,
            change_message=f"Status o'zgartirildi: KIRITILDI -> RAD ETILDI"
        )
        
        messages.error(request, f"Buyurtma №{order.order_number} **Rad Etildi**.")
        
        if order.created_by:
            Notification.objects.create(
                user=order.created_by,
                order=order,
                message=f"Siz kiritgan buyurtma №{order.order_number} Menejer tomonidan **RAD ETILDI**."
            )

        if order.assigned_workers.exists():
            for worker in order.assigned_workers.all():
                Notification.objects.create(
                    user=worker.user,
                    order=order,
                    message=f"Sizga tayinlangan buyurtma №{order.order_number} RAD ETILDI."
                )
        
    else:
        messages.warning(request, "Rad etishni faqat 'Kiritildi' statusidagi buyurtmadan boshlash mumkin.")
        
    return redirect('order_list')

from django.contrib.admin.models import LogEntry, CHANGE
from django.contrib.contenttypes.models import ContentType

@login_required
def order_start_production(request, pk):
    """3-Bosqich: Ishlab chiqarishga berish."""
    
    # 1. Kuzatuvchi tekshiruvi
    if is_observer(request.user):
        messages.error(request, "Kuzatuvchi rejimida bu amalni bajarish mumkin emas.")
        return redirect('order_list')
        
    order = get_object_or_404(Order, pk=pk)
    
    # 2. Ruxsatlarni tekshirish (Admin yoki Ishlab chiqarish boshlig'i)
    is_privileged = request.user.is_superuser or is_in_group(request.user, "Ishlab Chiqarish Boshlig'i")
    
    if not is_privileged:
        messages.error(request, "Ishlab chiqarishni boshlash uchun ruxsat yo'q.")
        return redirect('order_list')

    # 3. Statusni yangilash
    if order.status == 'TASDIQLANDI':
        order.status = 'ISHDA'
        order.save()
        
        # LogEntry.objects.log_action o'rniga standart .create ishlatamiz
        LogEntry.objects.create(
            user_id=request.user.id,
            content_type_id=ContentType.objects.get_for_model(order).pk,
            object_id=order.pk,
            object_repr=str(order),
            action_flag=CHANGE,
            change_message=f"Status o'zgartirildi: TASDIQLANDI -> ISHDA"
        )
        
        messages.info(request, f"Buyurtma №{order.order_number} ishlab chiqarishga berildi.")
        
        # 4. Bildirishnomalar
        if order.assigned_workers.exists():
            for worker in order.assigned_workers.all():
                Notification.objects.create(
                    user=worker.user,
                    order=order,
                    message=f"Buyurtma №{order.order_number} ISHGA TUSHDI. O'z ishingizni boshlashingiz mumkin."
                )
        
    else:
        messages.warning(request, "Ishlab chiqarishni faqat Tasdiqlangan buyurtmadan boshlash mumkin.")
        
    return redirect('order_list')
from django.contrib.admin.models import LogEntry, CHANGE
from django.contrib.contenttypes.models import ContentType

@login_required
def order_finish(request, pk):
    """4-Bosqich: Buyurtmani yakunlash."""
    # 1. Kuzatuvchi tekshiruvi
    if is_observer(request.user):
        messages.error(request, "Kuzatuvchi rejimida bu amalni bajarish mumkin emas.")
        return redirect('order_list')
        
    order = get_object_or_404(Order, pk=pk)
    
    # 2. Ruxsatlarni tekshirish (Admin yoki Ishlab chiqarish boshlig'i)
    is_privileged = request.user.is_superuser or is_in_group(request.user, "Ishlab Chiqarish Boshlig'i")
    
    if not is_privileged:
        messages.error(request, "Buyurtmani yakunlash uchun ruxsat yo'q.")
        return redirect('order_list')

    # 3. Statusni yangilash
    if order.status in ['ISHDA', 'USTA_TUGATDI']:
        # DIQQAT: Zanjir ishlashi uchun statusni USTA_TUGATDI qilib saqlash kerak
        order.status = 'USTA_TUGATDI' 
        order.save() # Modeldagi save() avtomatik yangi buyurtma ochishi mumkin
        
        # LogEntry xatosini tuzatish (log_action -> create)
        LogEntry.objects.create(
            user_id=request.user.id,
            content_type_id=ContentType.objects.get_for_model(order).pk,
            object_id=order.pk,
            object_repr=str(order),
            action_flag=CHANGE,
            change_message=f"Status o'zgartirildi: {order.get_status_display()}"
        )
        
        if order.worker_type in ['LIST', 'ESHIK', 'LIST_ESHIK']:
            messages.info(request, "Usta ishini tugatdi. Navbatdagi bosqich (Panel) avtomatik yaratildi.")

        messages.success(request, f"Buyurtma №{order.order_number} yakunlandi.")
        
        # 4. Bildirishnomalar
        try:
            manager_group = Group.objects.get(name='Menejer/Tasdiqlovchi') 
            for manager in manager_group.user_set.all():
                Notification.objects.create(
                    user=manager,
                    order=order,
                    message=f"Buyurtma №{order.order_number} usta tomonidan tugatildi."
                )
        except Group.DoesNotExist:
            pass

    else:
        messages.warning(request, "Buyurtmani yakunlash uchun u jarayonda bo'lishi kerak.")
        
    return redirect('order_list')

@login_required
def order_complete(request, pk):
    """Yakuniy bosqich: Buyurtmani to'liq Bajarildi deb belgilash."""
    # Kuzatuvchi tekshiruvi
    if is_observer(request.user):
        messages.error(request, "Kuzatuvchi rejimida bu amalni bajarish mumkin emas.")
        return redirect('order_list')
        
    order = get_object_or_404(Order, pk=pk)
    
    if not is_in_group(request.user, 'Menejer/Tasdiqlovchi'): 
        messages.error(request, "Buyurtmani yakunlash uchun ruxsat yo'q.")
        return redirect('order_list')

    if order.status == 'TAYYOR':
        order.status = 'BAJARILDI' 
        order.save()
        
        LogEntry.objects.log_action(
            user_id=request.user.id,
            content_type_id=ContentType.objects.get_for_model(order).pk,
            object_id=order.pk,
            object_repr=str(order),
            action_flag=CHANGE,
            change_message=f"Status o'zgartirildi: TAYYOR -> BAJARILDI (Yakuniy amal)"
        )
        
        messages.success(request, f"Buyurtma №{order.order_number} **BAJARILDI** deb belgilandi. Jarayon to'liq yakunlandi.")
        
        if order.created_by:
            Notification.objects.create(
                user=order.created_by,
                order=order,
                message=f"Siz kiritgan buyurtma №{order.order_number} Muvaffaqiyatli **BAJARILDI**."
            )
        
    else:
        messages.warning(request, "Buyurtma Bajarildi deb belgilanishi uchun u avval 'Tayyor' bo'lishi kerak.")
        
    return redirect('order_list')
@login_required
@user_passes_test(lambda u: u.is_superuser or is_in_group(u, 'Glavniy Admin'), login_url='/login/')
def order_delete(request, pk):
    """Buyurtmani Glavniy Admin tomonidan o'chirish."""
    # Kuzatuvchi tekshiruvi
    if is_observer(request.user):
        messages.error(request, "Kuzatuvchi rejimida bu amalni bajarish mumkin emas.")
        return redirect('order_list')
        
    order = get_object_or_404(Order, pk=pk)
    
    if request.method == 'POST':
        order_num = order.order_number
        
        LogEntry.objects.log_action(
            user_id=request.user.id,
            content_type_id=ContentType.objects.get_for_model(order).pk,
            object_id=order.pk, 
            object_repr=str(order),
            action_flag=DELETION,
            change_message=f"Buyurtma №{order_num} tizimdan o'chirildi."
        )
        
        order.delete()
        messages.error(request, f"Buyurtma №{order_num} tizimdan butunlay **O'CHIRILDI**.")
        return redirect('order_list')
        
    return render(request, 'orders/order_confirm_delete.html', {'order': order})

from decimal import Decimal
from datetime import datetime, timedelta
from django.db.models import Q
from django.utils import timezone
# ... (boshqa importlar)

@login_required
@user_passes_test(is_report_viewer_or_observer, login_url='/login/')
def weekly_report_view(request):
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    # Vaqt zonasidan xabardor sanalarni saqlash uchun o'zgaruvchilar
    start_datetime_aware = None
    end_datetime_aware = None

    # ----------------------------------------------------------------------
    # 💡 MUHIM TUZATISH: Sanani TZ-Aware qilish
    # ----------------------------------------------------------------------

    if start_date_str:
        try:
            # 1. Tanlangan sanani Naive Datetime obyektiga o'tkazamiz (00:00:00)
            start_datetime_naive = datetime.strptime(start_date_str, '%Y-%m-%d')
            # 2. Uni loyihaning TIME_ZONE zonasidan xabardor qilamiz (Masalan, Toshkent vaqti)
            start_datetime_aware = timezone.make_aware(start_datetime_naive)
        except ValueError:
             messages.error(request, "Boshlanish sana formati noto'g'ri.")
             start_date_str = None # Noto'g'ri bo'lsa filtrlashni to'xtatamiz

    if end_date_str:
        try:
            # 1. Tanlangan sanani Naive Datetime obyektiga o'tkazamiz (+1 kun)
            end_datetime_naive = datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1)
            # 2. Uni loyihaning TIME_ZONE zonasidan xabardor qilamiz
            end_datetime_aware = timezone.make_aware(end_datetime_naive)
        except ValueError:
            messages.error(request, "Tugash sana formati noto'g'ri.")
            end_date_str = None
            
    # ----------------------------------------------------------------------
    # 1. Umumiy Buyurtmalar (created_at bo'yicha) filtrlash
    # ----------------------------------------------------------------------
    orders = Order.objects.all().select_related('created_by')
    filter_q = Q()
    
    if start_datetime_aware:
        # created_at__gte ni TZ-aware qiymat bilan solishtiramiz
        filter_q &= Q(created_at__gte=start_datetime_aware) 
    if end_datetime_aware:
        # created_at__lt ni TZ-aware qiymat bilan solishtiramiz
        filter_q &= Q(created_at__lt=end_datetime_aware) 
            
    report_orders = orders.filter(filter_q).order_by('-created_at')
    
    # ... (Umumiy statistikani hisoblash)
    
    # ----------------------------------------------------------------------
    # 2. Ustalar Ish Faoliyati Hisoboti (worker_finished_at bo'yicha)
    # ----------------------------------------------------------------------
    worker_report_orders = Order.objects.filter(
        status__in=['TAYYOR', 'BAJARILDI']
    ).filter(
        worker_finished_at__isnull=False 
    ).prefetch_related(
        'assigned_workers__user'
    ) 
    
    worker_filter_q = Q()
    # Yuqorida tayyorlangan TZ-aware obyektlarni qayta ishlatamiz!
    if start_datetime_aware:
        worker_filter_q &= Q(worker_finished_at__gte=start_datetime_aware)
    if end_datetime_aware:
        worker_filter_q &= Q(worker_finished_at__lt=end_datetime_aware)
            
    worker_report_orders = worker_report_orders.filter(worker_filter_q)
    
    # ... (Qolgan loop va kontekst mantig'i)
    
    # Kontekstni yangilash
    context = {
        # ... (boshqa kontekstlar)
        # Template uchun sanalarni string formatida qaytarish
        'start_date': start_date_str,
        'end_date': end_date_str,
        # ...
    }

    return render(request, 'orders/weekly_report_view.html', context)
# orders/views.py

from django.shortcuts import render, redirect 
from django.contrib import messages
from django.contrib.auth.decorators import login_required
# ... (Boshqa importlar qolaversin) ...

# from .forms import MaterialInflowForm, MaterialOutflowForm # Bularni o'chirib tashlaymiz
from .forms import MaterialTransactionForm # YANGI formani import qilamiz

# ... (material_sarfi_report funksiyasi qolaversin) ...

# ===================================================================
# 🔄 YAGONA VIEW: MATERIAL HARAKATINI YARATISH
# ===================================================================

from .forms import (
    OrderForm, 
    StartImageUploadForm, 
    FinishImageUploadForm, 
    OrderStatusForm, 
    MaterialTransactionForm,)
import json
# views.py

# views.py
from django.db import transaction as db_transaction
from django.contrib import messages
import json

# views.py
# views.py - material_transaction_create view ni yangilash
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction as db_transaction
import json
from .forms import MaterialTransactionForm
from .models import Material

import json
import uuid  # ⬅️ Unikal kod uchun shart
from django.db import transaction as db_transaction
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages

import uuid
import json
from django.shortcuts import render, redirect
from django.contrib import messages
from django.db import transaction as db_transaction  # Nomini moslashtirdik
from django.contrib.auth.decorators import login_required
from .models import Material, MaterialTransaction
from .forms import MaterialTransactionForm
from django.http import JsonResponse
from django.views.decorators.http import require_GET
import json

@login_required
def material_transaction_create(request):
    """Material kirim/chiqim tranzaksiyasini yaratish."""
    
    if request.method == 'POST':
        form = MaterialTransactionForm(request.POST)
        
        if form.is_valid():
            try:
                with db_transaction.atomic():
                    transaction_obj = form.save(commit=False)
                    transaction_obj.performed_by = request.user
                    
                    # Materialni olish
                    material = transaction_obj.material
                    quantity = transaction_obj.quantity_change
                    transaction_type = transaction_obj.transaction_type
                    
                    # DEBUG
                    print(f"Material: {material.name} (ID: {material.id})")
                    print(f"Quantity: {quantity}")
                    print(f"Type: {transaction_type}")
                    
                    # Barcode yaratish
                    create_barcode = request.POST.get('create_batch_barcode') == 'on'
                    if transaction_type == 'IN' and create_barcode:
                        import uuid
                        new_code = f"P-{uuid.uuid4().hex[:8].upper()}"
                        transaction_obj.transaction_barcode = new_code
                    
                    # Qoldiqni yangilash
                    if transaction_type == 'IN':
                        material.quantity += quantity
                        message_type = "✅ Kirim"
                    else:  # OUT
                        if material.quantity < quantity:
                            raise ValueError(
                                f"Omborda yetarli qoldiq yo'q! "
                                f"Mavjud: {material.quantity} {material.unit}, "
                                f"So'ralgan: {quantity}"
                            )
                        material.quantity -= quantity
                        message_type = "📤 Chiqim"
                    
                    material.save()
                    transaction_obj.save()
                    
                    messages.success(request, 
                        f"{message_type} muvaffaqiyatli bajarildi. "
                        f"Material: {material.name}, "
                        f"Yangi qoldiq: {material.quantity} {material.unit}"
                    )
                    return redirect('material_list')
                    
            except ValueError as e:
                messages.error(request, f"⚠️ {str(e)}")
            except Exception as e:
                messages.error(request, f"❌ Texnik xatolik: {str(e)}")
        else:
            for field, errors in form.errors.items():
                field_name = form.fields[field].label if field in form.fields else field
                messages.error(request, f"{field_name}: {', '.join(errors)}")
    
    else:
        form = MaterialTransactionForm()
    
    # Material ma'lumotlarini JSON formatda yuborish
    materials = Material.objects.all().select_related('category').order_by('name')
    material_data = {}
    
    for mat in materials:
        material_data[str(mat.id)] = {
            'name': mat.name,
            'quantity': float(mat.quantity),
            'unit': mat.unit,
            'category': mat.category.name if mat.category else 'Kategoriyasiz',
            'product_name': mat.product_name if hasattr(mat, 'product_name') else '',
        }
    
    return render(request, 'orders/material_transaction_create.html', {
        'form': form,
        'material_data_json': json.dumps(material_data, ensure_ascii=False),
    })


# ✅ AJAX endpoint material ma'lumotlari uchun
@require_GET
@login_required
def get_material_details(request, material_id):
    """Material ma'lumotlarini JSON formatda qaytarish."""
    try:
        material = Material.objects.select_related('category').get(id=material_id)
        
        data = {
            'id': material.id,
            'name': material.name,
            'code': material.code,
            'quantity': float(material.quantity),
            'unit': material.unit,
            'category': material.category.name if material.category else 'Kategoriyasiz',
            'product_name': material.product_name if hasattr(material, 'product_name') else '',
            'price_per_unit': float(material.price_per_unit) if material.price_per_unit else 0,
            'min_stock_level': float(material.min_stock_level) if material.min_stock_level else 0,
            'success': True
        }
        return JsonResponse(data)
    except Material.DoesNotExist:
        return JsonResponse({'error': 'Material topilmadi', 'success': False}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e), 'success': False}, status=500)


# 🔴 YANGI: Material yaratish view
@login_required
def material_create(request):
    """Yangi material yaratish."""
    if request.method == 'POST':
        form = MaterialForm(request.POST)
        if form.is_valid():
            material = form.save()
            messages.success(request, f"✅ Material '{material.name}' muvaffaqiyatli yaratildi.")
            return redirect('material_list')
    else:
        form = MaterialForm()
    
    context = {'form': form}
    return render(request, 'orders/material_form.html', context)


# 🔴 YANGI: Material tahrirlash view
@login_required
def material_edit(request, pk):
    """Materialni tahrirlash."""
    material = get_object_or_404(Material, pk=pk)
    
    if request.method == 'POST':
        form = MaterialForm(request.POST, instance=material)
        if form.is_valid():
            material = form.save()
            messages.success(request, f"✅ Material '{material.name}' muvaffaqiyatli yangilandi.")
            return redirect('material_list')
    else:
        form = MaterialForm(instance=material)
    
    context = {'form': form, 'material': material}
    return render(request, 'orders/material_form.html', context)

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import F, ExpressionWrapper, DecimalField, Sum, Q, Avg, Count, IntegerField, Case, When
from django.db.models.functions import Coalesce # Coalesce uchun import
from datetime import timedelta
from django.utils import timezone
from django.core.paginator import Paginator
from decimal import Decimal # Decimal ishlatish uchun

# Modellar importi (Material va MaterialTransaction)
from .models import Material, MaterialTransaction 
# Eslatma: Agar modellar boshqa joyda bo'lsa, uni yuqoriga qo'ying

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Q, F, DecimalField, IntegerField, Count, Avg, ExpressionWrapper, Case, When
from django.db.models.functions import Coalesce
from django.utils import timezone
from datetime import timedelta
from django.core.paginator import Paginator
from decimal import Decimal # DecimalField uchun kerak

# Material va MaterialTransaction modellarini import qiling (agar yuqorida bo'lmasa)
# from .models import Material, MaterialTransaction 

@login_required
def material_list(request):
    """
    Omborxona materiallari va tranzaksiyalarini ko'rsatish:
    Barcode va to'g'rilangan related_name bilan.
    """
    filter_low_stock = request.GET.get('low_stock') == 'true'
    filter_has_stock = request.GET.get('has_stock') == 'true'
    filter_type = request.GET.get('type', '') 
    
    current_date = timezone.now()
    today_start = current_date.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = current_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    thirty_days_ago = current_date - timedelta(days=30)
    week_start = current_date - timedelta(days=7)
    
    # 1. MATERIALLAR RO'YXATI
    # ✅ related_name='transactions' bo'lgani uchun 'transactions' so'zi ishlatiladi
    base_materials_qs = Material.objects.annotate(
        difference=ExpressionWrapper(
            F('quantity') - F('min_stock_level'),
            output_field=DecimalField(max_digits=15, decimal_places=3)
        ),
        total_value=ExpressionWrapper(
            F('quantity') * F('price_per_unit'),
            output_field=DecimalField(max_digits=15, decimal_places=2)
        ), 
    ).order_by('name') 

    materials = base_materials_qs
    if filter_low_stock:
        materials = materials.filter(quantity__lt=F('min_stock_level'))
    if filter_has_stock:
        materials = materials.filter(quantity__gt=0)
    
    paginator = Paginator(materials, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # 2. KIRIM TRANZAKSIYALARI (Barcode qo'shilgan)
    incoming_transactions_raw = MaterialTransaction.objects.filter(
        transaction_type='IN'
    ).select_related('material').order_by('-timestamp')[:100]
    
    income_materials = []
    for tx in incoming_transactions_raw:
        material = tx.material
        quantity = tx.quantity_change
        unit_price = material.price_per_unit if material else Decimal('0') 
        
        income_materials.append({
            'id': tx.id,
            'date': tx.timestamp,
            'barcode': tx.transaction_barcode,  # 🔴 BARCODE SHU YERDA
            'material': {
                'name': material.name if material else 'Noma\'lum',
                'product_name': material.product_name if material and hasattr(material, 'product_name') else '',
                'unit': material.unit if material else ''
            },
            'quantity': quantity,
            'price': unit_price,
            'total': quantity * unit_price,
            'notes': tx.notes or '',
            'supplier': tx.received_by or ''
        })
    
    # 3. CHIQIM TRANZAKSIYALARI
    outgoing_transactions_raw = MaterialTransaction.objects.filter(
        transaction_type='OUT'
    ).select_related('material').order_by('-timestamp')[:100]

    outcome_materials = []
    for tx in outgoing_transactions_raw:
        material = tx.material
        quantity = abs(tx.quantity_change)
        unit_price = material.price_per_unit if material else Decimal('0') 
        outcome_materials.append({
            'id': tx.id,
            'date': tx.timestamp,
            'barcode': tx.transaction_barcode,
            'material': {
                'name': material.name if material else 'Noma\'lum',
                'unit': material.unit if material else ''
            },
            'quantity_change': tx.quantity_change,
            'unit_price': unit_price,
            'total_value': quantity * unit_price,
            'notes': tx.notes or '',
            'department': tx.received_by or '' 
        })
        
    # 4. TOP MATERIALLAR (Xatolik tuzatilgan qism)
    # ✅ materialtransaction o'rniga transactions ishlatildi
    # views.py 1441-qator atrofini shunday o'zgartiring:

    top_materials_qs = Material.objects.annotate(
        # total_incoming hisobi
        total_incoming=Coalesce(
            Sum('materialtransaction__quantity_change', 
                filter=Q(materialtransaction__transaction_type='IN')), 
            Decimal('0'), 
            output_field=DecimalField(max_digits=15, decimal_places=3)
        ),
        # total_outgoing hisobi
        total_outgoing=Coalesce(
            Sum('materialtransaction__quantity_change', 
                filter=Q(materialtransaction__transaction_type='OUT')), 
            Decimal('0'), 
            output_field=DecimalField(max_digits=15, decimal_places=3)
        ),
        # incoming_value hisobi
        incoming_value=ExpressionWrapper(
            Coalesce(
                Sum('materialtransaction__quantity_change', 
                    filter=Q(materialtransaction__transaction_type='IN')), 
                Decimal('0')
            ) * F('price_per_unit'),
            output_field=DecimalField(max_digits=15, decimal_places=2) 
        ),
        # outgoing_value hisobi
        outgoing_value=ExpressionWrapper(
            Coalesce(
                Sum('materialtransaction__quantity_change', 
                    filter=Q(materialtransaction__transaction_type='OUT')), 
                Decimal('0')
            ) * F('price_per_unit'),
            output_field=DecimalField(max_digits=15, decimal_places=2) 
        )
    )
    
    top_incoming_materials = top_materials_qs.filter(total_incoming__gt=0).order_by('-total_incoming')[:10]
    top_outgoing_materials = top_materials_qs.filter(total_outgoing__gt=0).order_by('-total_outgoing')[:10]
    
    context = {
        'materials': page_obj,
        'page_obj': page_obj,
        'income_materials': income_materials,
        'outcome_materials': outcome_materials,
        'top_incoming_materials': top_incoming_materials,
        'top_outgoing_materials': top_outgoing_materials,
        'current_date': current_date,
        'title': 'Omborxona Boshqaruvi',
    }
    
    return render(request, 'orders/material_list.html', context)
# orders/views.py

from django.shortcuts import render
# from .models import Material # Modellar ham import qilingan bo'lishi kerak

# ... (Boshqa view funksiyalaringiz)

# orders/views.py

from django.shortcuts import render
from django.db.models import Sum
from django.utils import timezone
from datetime import timedelta
from .models import Material, MaterialTransaction # Modellar shu yerda import qilinishi kerak

# orders/views.py

from django.shortcuts import render
from django.contrib.auth.decorators import login_required

@login_required
def fast_scanner_view(request):
    """
    Tezkor Skanerlash uchun alohida sahifani render qiladi.
    Bu sahifada faqat Kirim/Chiqim rejimi va Skanerlash maydoni bo'ladi.
    """
    context = {
        'title': 'Tezkor Skanerlash Markazi',
    }
    # orders/fast_scanner.html shablonini chaqirish
    return render(request, 'orders/fast_scanner.html', context)


# views.py ga qo'shing
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json

# orders/views.py
from orders.models import Material
from django.http import JsonResponse
from django.db import transaction as db_transaction
from django.contrib.auth.decorators import login_required

@login_required
def find_material_by_code_api(request):
    if request.method == 'GET':
        code = request.GET.get('code', '').strip()
        
        if not code:
            return JsonResponse({'success': False, 'error': 'Kod kiritilmadi.'}, status=400)
        
        try:
            # 1. Materialni topishga urinish (product_name yoki code orqali)
            material = Material.objects.filter(
                product_name__iexact=code
            ).first() or Material.objects.filter(code__iexact=code).first()
            if material:
                is_new = False
            else:
                raise Material.DoesNotExist
        except Material.DoesNotExist:
            # 2. Agar topilmasa, uni avtomatik yaratish!
            try:
                with db_transaction.atomic():
                    material = Material.objects.create(
                        name=f"Yangi Material (Kod: {code})",
                        product_name=code, # Kodni bu yerga saqlash
                        unit='dona',
                        quantity=0, # Boshlang'ich qoldiq
                        price_per_unit=0
                        # Agar modelda boshqa majburiy maydonlar bo'lsa, ularni qo'shing (masalan, category_id)
                    )
                is_new = True
            except Exception as create_error:
                return JsonResponse({
                    'success': False, 
                    'error': f"Avtomatik yaratishda xato: {create_error}"
                }, status=500)
        
        # 3. Natijani qaytarish
        return JsonResponse({
            'success': True,
            'material_id': material.id,
            'material_name': material.name,
            'material_code': material.product_name, # Kod sifatida product_name ni yuborish
            'material_unit': material.unit,
            'scanned_raw_code': code, # <-- Shu yerda code o'zgaruvchisi yuborilmoqda
            # ... boshqa maydonlar
            'is_new': is_new
        })
        
    return JsonResponse({'success': False, 'error': 'Faqat GET so\'rovi qabul qilinadi.'}, status=405)

@login_required
@csrf_exempt  # Faqat test uchun
def save_scanned_transactions_api(request):
    """API: Skanerlangan tranzaksiyalarni saqlash"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            # Bu yerda ma'lumotlarni saqlash logikasi
            # Misol:
            # for item in data['items']:
            #     Transaction.objects.create(...)
            
            return JsonResponse({
                'success': True,
                'message': f"{len(data.get('items', []))} ta element saqlandi"
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': str(e)
            })
# orders/views.py

from django.shortcuts import render, redirect, get_object_or_404
from .forms import MaterialTransactionForm # Forma mavjudligini taxmin qilamiz
# ... (boshqa importlar)

@login_required
def add_transaction_view(request):
    """
    Yangi omborxona harakatini (kirim yoki chiqim) qo'shish.
    """
    if request.method == 'POST':
        form = MaterialTransactionForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('material_list') # Muvaffaqiyatli saqlangandan keyin inventarizatsiya sahifasiga qaytish
    else:
        form = MaterialTransactionForm()
        
    context = {
        'title': 'Yangi Harakat Qo\'shish (Kirim/Chiqim)',
        'form': form
    }
    return render(request, 'orders/add_transaction.html', context)



# orders/views.py

from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.db import transaction
from .models import Material, MaterialTransaction
from django.shortcuts import get_object_or_404



@require_POST
@login_required
def remove_transaction_view(request):
    """
    Omborxona materialini chiqim qilish (omborxona zaxirasidan olib tashlash)
    """
    if not request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': False, 'error': 'Invalid Request'}, status=400)
    
    try:
        material_id = request.POST.get('material_id')
        quantity = float(request.POST.get('quantity'))
        reason = request.POST.get('reason', 'Chiqim sababi ko\'rsatilmadi')
        
        material = get_object_or_404(Material, pk=material_id)
        
        if quantity <= 0:
            return JsonResponse({'success': False, 'error': 'Noto\'g\'ri miqdor kiritildi'}, status=400)
        
        with transaction.atomic():
            # Zaxira yetarli yoki yo'qligini tekshirish
            if material.quantity < quantity:
                return JsonResponse({'success': False, 'error': f'Zaxirada yetarli {material.unit} mavjud emas. (Mavjud: {material.quantity})'}, status=400)
            
            # 1. Zaxirani yangilash
            material.quantity -= quantity
            material.save()
            
            # 2. Tranzaksiyani yaratish (Chiqim)
            MaterialTransaction.objects.create(
                material=material,
                transaction_type='OUT', # Chiqim
                quantity_change=quantity,
                performed_by=request.user if request.user.is_authenticated else None,
                notes=reason
            )
        
        return JsonResponse({'success': True, 'message': 'Chiqim muvaffaqiyatli amalga oshirildi.'})
        
    except Material.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Material topilmadi.'}, status=404)
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Miqdor noto\'g\'ri formatda.'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Kutilmagan xato: {str(e)}'}, status=500)
    


@login_required
def material_transaction_delete(request, pk):
    """Material tranzaksiyasini o'chirish."""
    transaction = get_object_or_404(MaterialTransaction, pk=pk)
    
    if request.method == 'POST':
        try:
            # Qaytarish logikasi (agar kerak bo'lsa)
            material = transaction.material
            if transaction.transaction_type == 'IN':
                material.quantity -= transaction.quantity_change
            else:  # OUT
                material.quantity += transaction.quantity_change
            
            material.save()
            transaction.delete()
            
            messages.success(request, "✅ Tranzaksiya muvaffaqiyatli o'chirildi.")
            return redirect('material_list')
            
        except Exception as e:
            messages.error(request, f"❌ Xatolik: {str(e)}")
    
    context = {
        'transaction': transaction,
    }
    
    return render(request, 'orders/material_transaction_confirm_delete.html', context)
def get_material_data():
    """
    Material ma'lumotlarini JSON uchun tayyorlash
    """
    material_objects = Material.objects.all().select_related('category').values(
        'id', 'name', 'unit', 'quantity', 'category__name'
    )
    
    material_data = {
        str(m['id']): {
            'name': m['name'], 
            'unit': m['unit'], 
            'quantity': float(m['quantity']) if m['quantity'] is not None else 0,
            'category': m['category__name'] if m['category__name'] else 'Kategoriyasiz'
        } 
        for m in material_objects
    }
    
    return material_data


# orders/views.py


from django.db.models.functions import Coalesce # ⬅️ Mana bu qatorni qo'shing
from decimal import Decimal # ✅ Decimal to'g'ri import qilindi
from decimal import Decimal
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.shortcuts import render
from .models import Order
from django.db.models import Sum, Q, DecimalField
from django.db.models.functions import Coalesce
from decimal import Decimal
from django.shortcuts import render
from .models import Order

def material_sarfi_report(request):
    """
    Asosiy buyurtmalar (parent) asosida materiallar sarfini 
    aniq koeffitsientlar bilan hisoblaydigan hisobot.
    """
    
    # 1. FAQAT ASOSIY BUYURTMALARNI FILTRLASH (Dublikat bo'lmasligi uchun)
    parent_orders = Order.objects.filter(parent_order__isnull=True)

    # 2. BAZAGA FAQAT 1 MARTA MUROJAAT QILIB, BARCHA SUMMALARNI OLAMIZ
    # Bu usul filter(thickness=...).aggregate() dan 5 baravar tezroq ishlaydi
    stats = parent_orders.aggregate(
        kv_5=Coalesce(Sum('panel_kvadrat', filter=Q(panel_thickness='5')), Decimal('0')),
        kv_8=Coalesce(Sum('panel_kvadrat', filter=Q(panel_thickness='8')), Decimal('0')),
        kv_10=Coalesce(Sum('panel_kvadrat', filter=Q(panel_thickness='10')), Decimal('0')),
        kv_15=Coalesce(Sum('panel_kvadrat', filter=Q(panel_thickness='15')), Decimal('0')),
        total_kv=Coalesce(Sum('panel_kvadrat'), Decimal('0'))
    )

    # 3. STATS'DAN QIYMATLARNI ALOHIDA O'ZGARUVCHILARGA OLAMIZ
    sum_kv_5 = stats['kv_5']
    sum_kv_8 = stats['sum_8'] if 'sum_8' in stats else stats['kv_8'] # xavfsizlik uchun
    sum_kv_10 = stats['kv_10']
    sum_kv_15 = stats['kv_15']
    total_kvadrat = stats['total_kv']

    # 4. SIRYO SARFINI QALINLIK BO'YICHA ANIQ HISOBLASH
    # Formulalar: 5cm->x2, 8cm->x3, 10cm->x4, 15cm->x6
    siryo_5_sarfi = sum_kv_5 * Decimal('2')
    siryo_8_sarfi = sum_kv_8 * Decimal('3')
    siryo_10_sarfi = sum_kv_10 * Decimal('4')
    siryo_15_sarfi = sum_kv_15 * Decimal('6')

    # Jami Siryo (kg)
    jami_siryo_sarfi = siryo_5_sarfi + siryo_8_sarfi + siryo_10_sarfi + siryo_15_sarfi

    # 5. JAMI LIST SARFI (m²): (Total Kvadrat * 2) + 10 metr zapas
    # Faqat buyurtma mavjud bo'lsagina zapas qo'shiladi
    if total_kvadrat > 0:
        jami_list_sarfi = (total_kvadrat * Decimal('2')) + Decimal('10')
    else:
        jami_list_sarfi = Decimal('0')

    # 6. CONTEXT - HAMMA MA'LUMOTLARNI TEMPLATE'GA YUBORAMIZ
    context = {
        # Jami natijalar
        'total_kvadrat': total_kvadrat,
        'jami_list_sarfi': jami_list_sarfi,
        'jami_siryo_sarfi': jami_siryo_sarfi,
        
        # Har bir qalinlik bo'yicha kvadratlar
        'sum_kv_5': sum_kv_5,
        'sum_kv_8': sum_kv_8,
        'sum_kv_10': sum_kv_10,
        'sum_kv_15': sum_kv_15,

        # Har bir qalinlik bo'yicha siryo sarfi
        'siryo_5_sarfi': siryo_5_sarfi,
        'siryo_8_sarfi': siryo_8_sarfi,
        'siryo_10_sarfi': siryo_10_sarfi,
        'siryo_15_sarfi': siryo_15_sarfi,
    }
    
    return render(request, 'orders/material_sarfi_report.html', context)

from django.db.models import Q, Sum, Count, F
from django.utils.dateparse import parse_date
from datetime import timedelta

from django.db.models import Sum, Count, F

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, F, Q
from .models import Order

from django.db.models import Sum, Count, F, Q

@login_required
def worker_activity_report_view(request):
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    # 1. Archive dagi kabi statuslar
    archive_statuses = ['BAJARILDI', 'USTA_TUGATDI', 'TAYYOR']
    
    # 2. Barcha buyurtmalar (Asosiy va ichki)
    # Xuddi Archive dagi kabi filtrlaymiz
    orders = Order.objects.filter(status__in=archive_statuses)

    # 3. Sana bo'yicha filtr (Agar sana tanlangan bo'lsa)
    if start_date:
        orders = orders.filter(worker_finished_at__date__gte=start_date)
    if end_date:
        orders = orders.filter(worker_finished_at__date__lte=end_date)

    # 4. Ustalar bo'yicha guruhlash
    # Har bir orderning 'assigned_workers' maydonidan foydalanamiz
    worker_report_list = orders.values(
        'assigned_workers__id'
    ).annotate(
        first_name=F('assigned_workers__user__first_name'),
        last_name=F('assigned_workers__user__last_name'),
        username=F('assigned_workers__user__username'),
        total_order_count=Count('id'),
        total_finished_kvadrat=Sum('panel_kvadrat')
    ).filter(assigned_workers__id__isnull=False).order_by('-total_finished_kvadrat')

    # 5. Jami summa
    total_kv = orders.aggregate(Sum('panel_kvadrat'))['panel_kvadrat__sum'] or 0

    context = {
        "title": "Ustalar Ish Faoliyati Hisoboti",
        'worker_report_list': worker_report_list,
        'total_finished_kvadrat': total_kv,
        'start_date': start_date,
        'end_date': end_date,
    }
    return render(request, 'orders/weekly_report_view.html', context)

@login_required
@user_passes_test(is_report_viewer_or_observer, login_url='/')  # ✅ YANGI
def export_worker_activity_csv(request):
    """
    Ustalarning ish faoliyati hisobotini CSV fayl shaklida eksport qiladi.
    """
    # 1. CSV javobini tayyorlash
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="usta_faoliyat_hisoboti_{datetime.now().strftime("%Y-%m-%d")}.csv"'
    
    writer = csv.writer(response)
    
    # Jadval sarlavhalari
    writer.writerow([
        'T/r', 
        'Usta F.I.Sh.', 
        'Bajarilgan Kvadratura (m²)', 
        'Bajarilgan Buyurtmalar Soni'
    ])

    # 2. Filtrlash shartlarini yaratish
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    
    # ✅ Xato tuzatildi: Statusni to'g'ri string qiymati bilan filtrlash
    worker_filter_q = Q(status='FINISHED') 
    
    start_date = None
    end_date = None

    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            worker_filter_q &= Q(updated_at__date__gte=start_date)
        except ValueError:
            pass 

    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            worker_filter_q &= Q(updated_at__date__lte=end_date)
        except ValueError:
            pass

    # 3. Hisobot ma'lumotlarini olish
    worker_report_list = Order.objects.filter(worker_filter_q).exclude(assigned_workers__isnull=True).values(
        'assigned_workers__user__first_name',
        'assigned_workers__user__last_name'
    ).annotate(
    total_finished_kvadrat=Sum('panel_kvadrat', default=0), 
    total_order_count=Count('id')

    ).order_by('assigned_workers__user__first_name')
    
    # 4. CSV ga ma'lumotlarni yozish
    for i, worker in enumerate(worker_report_list):
        writer.writerow([
            i + 1,
            f"{worker['assigned_workers__user__first_name']} {worker['assigned_workers__user__last_name']}",
            f"{worker['total_finished_kvadrat']:.2f}",
            worker['total_order_count']
        ])
        
    return response

@login_required
@user_passes_test(lambda u: u.is_superuser or is_in_group(u, 'Glavniy Admin'), login_url='/login/')
def export_orders_csv(request):
    """Buyurtmalarni CSV formatida eksport qilish (oxirgi 7 kunlik)."""
    # Kuzatuvchi tekshiruvi
    if is_observer(request.user):
        messages.error(request, "Kuzatuvchi rejimida bu amalni bajarish mumkin emas.")
        return redirect('order_list')
        
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="EcoProm_Buyurtmalar_Hisboti_7kunlik.csv"'

    writer = csv.writer(response, delimiter=';') 

    writer.writerow([
        "Buyurtma Raqami", 
        "Xaridor Nomi", 
        "Kvadrat (m²)", 
        "Summa (so'm)", 
        "Status", 
        "Kiritilgan Sana",
        "Kiritgan Xodim"
    ])

    seven_days_ago = date.today() - timedelta(days=7)
    orders = Order.objects.filter(
        created_at__gte=seven_days_ago
    ).order_by('-created_at')

    for order in orders:
        writer.writerow([
            order.order_number,
            order.customer_name,
            order.panel_kvadrat,
            order.total_price,
            order.get_status_display(),
            order.created_at.strftime("%Y-%m-%d %H:%M"),
            order.created_by.get_full_name() if order.created_by else "Noma'lum", 
        ])

    return response

@login_required
@user_passes_test(is_report_viewer_or_observer, login_url='/login/')
def sales_report_view(request):
    """Vaqt oralig'i bo'yicha sotuv hisobotini ko'rsatish va filtrlash."""
    
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    today = date.today()
    
    try:
        if start_date_str and end_date_str:
            start_date = date.fromisoformat(start_date_str)
            end_date = date.fromisoformat(end_date_str)
        else:
            start_date = today - timedelta(days=30)
            end_date = today
    except ValueError:
        messages.error(request, "Noto'g'ri sana formati kiritildi. Iltimos, YYYY-MM-DD formatida kiriting.")
        start_date = today - timedelta(days=30)
        end_date = today

    # 🔴 Asosiy buyurtmalar
    main_orders = Order.objects.filter(
        parent_order__isnull=True,  # Faqat asosiy buyurtmalar
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
    ).order_by('-created_at')

    # 🔴 Child buyurtmalar (alohida)
    child_orders = Order.objects.filter(
        parent_order__isnull=False,  # Faqat child buyurtmalar
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
    ).order_by('-created_at')

    # 🔴 Barcha buyurtmalar (umumiy ko'rish uchun)
    all_orders = Order.objects.filter(
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
    ).order_by('-created_at')

    total_orders_count = main_orders.count()
    total_square = main_orders.aggregate(Sum('panel_kvadrat'))['panel_kvadrat__sum'] or 0
    total_revenue = main_orders.aggregate(Sum('total_price'))['total_price__sum'] or 0

    context = {
        "title": "Sotuv Hisoboti (Vaqt Oralig'i)",
        'report_orders': main_orders,  # 🔴 Faqat asosiylar ko'rsatiladi
        'child_orders': child_orders,  # 🔴 Child buyurtmalar (alohida)
        'all_orders': all_orders,  # 🔴 Barcha buyurtmalar
        'start_date': start_date.strftime('%Y-%m-%d'),
        'end_date': end_date.strftime('%Y-%m-%d'),
        'total_orders_count': total_orders_count,
        'total_square': total_square,
        'total_revenue': total_revenue,
        'is_glavniy_admin': True,
        'today': timezone.now().date(),
        'is_observer': is_observer(request.user),
    }
    return render(request, 'orders/sales_report.html', context)



@login_required
@user_passes_test(lambda u: u.is_superuser or is_in_group(u, 'Glavniy Admin'), login_url='/login/')
def product_audit_log_view(request):
    """Mahsulot/Buyurtma o'zgarishlari jurnalini ko'rish funksiyasi."""
    # Kuzatuvchi tekshiruvi
    if is_observer(request.user):
        messages.error(request, "Kuzatuvchi rejimida bu amalni bajarish mumkin emas.")
        return redirect('order_list')
        
    try:
        order_content_type = ContentType.objects.get(app_label='orders', model='order')
        
        log_entries = LogEntry.objects.filter(
            content_type=order_content_type
        ).select_related('user').order_by('-action_time')
        
        for log in log_entries:
            try:
                log.related_object_name = Order.objects.get(pk=log.object_id).order_number
            except Order.DoesNotExist:
                log.related_object_name = log.object_repr
    except ContentType.DoesNotExist:
        log_entries = []
        messages.error(request, "Buyurtma (Order) modeli uchun ContentType topilmadi. `LogEntry` filtrlanmaydi.")
    

    context = {
        "title": "Mahsulot O'zgarishlari Jurnali (Audit Log)",
        'log_entries': log_entries,
        'is_glavniy_admin': True,
        'is_observer': False, 
    }
    
    return render(request, 'orders/product_audit_log.html', context)

@login_required
@user_passes_test(lambda u: u.is_superuser or is_in_group(u, 'Glavniy Admin'), login_url='/login/')
def export_audit_log_csv(request):
    """Audit Log yozuvlarini CSV formatida eksport qilish."""
    # Kuzatuvchi tekshiruvi
    if is_observer(request.user):
        messages.error(request, "Kuzatuvchi rejimida bu amalni bajarish mumkin emas.")
        return redirect('order_list')
        
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="EcoProm_Audit_Log_Hisoboti.csv"'

    writer = csv.writer(response, delimiter=';') 

    writer.writerow([
        "Harakat Vaqti", 
        "Foydalanuvchi", 
        "Harakat Turi", 
        "Buyurtma Raqami/Obyekt", 
        "O'zgarish Tafsiloti (Change Message)"
    ])

    try:
        order_content_type = ContentType.objects.get(app_label='orders', model='order')
        log_entries = LogEntry.objects.filter(
            content_type=order_content_type
        ).select_related('user').order_by('-action_time')
    except ContentType.DoesNotExist:
        messages.error(request, "Buyurtma (Order) modeli ContentType topilmadi. Eksport qilish bekor qilindi.")
        return redirect('product_audit_log_view') 

    def get_action_type(flag):
        if flag == ADDITION:
            return 'Yaratildi (ADDITION)'
        elif flag == CHANGE:
            return 'Tahrirlandi (CHANGE)'
        elif flag == DELETION:
            return 'Oʻchirildi (DELETION)'
        return 'Nomaʼlum'

    for log in log_entries:
        
        object_identifier = ''
        try:
            object_identifier = Order.objects.get(pk=log.object_id).order_number
        except Order.DoesNotExist:
            object_identifier = f"O'chirilgan obyekti (ID: {log.object_id})"

        
        writer.writerow([
            log.action_time.strftime("%Y-%m-%d %H:%M:%S"),
            log.user.get_full_name() or log.user.username,
            get_action_type(log.action_flag),
            object_identifier,
            log.change_message.replace('\r\n', ' ').replace('\n', ' ') 
        ])

    return response

from django.db.models import F

from django.db.models import F, Q
from django.db.models import F, Q, Sum, DecimalField, ExpressionWrapper, Value
from django.db.models.functions import Coalesce
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator

import io
import pandas as pd
from django.db.models import F, Q, Sum, DecimalField, ExpressionWrapper, Value
from django.db.models.functions import Coalesce
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse

@login_required
def debt_report(request):
    # 1. Ruxsatlarni tekshirish
    user = request.user
    is_admin = user.is_superuser or is_in_group(user, 'Glavniy Admin')
    is_manager = is_in_group(user, 'Menejer/Tasdiqlovchi')
    
    if not (is_admin or is_manager):
        messages.error(request, "Sizga bu sahifani ko'rishga ruxsat yo'q!")
        return redirect('order_list')
    
    # 2. Parametrlarni olish
    search_query = request.GET.get('q', '').strip()
    sort_by = request.GET.get('sort', 'debt_desc')
    
    # 3. Asosiy QuerySet - Hisob-kitobni SQL darajasida qilish
    debts = Order.objects.annotate(
        calculated_debt=ExpressionWrapper(
            Coalesce(F('total_price'), Value(0, output_field=DecimalField())) - 
            Coalesce(F('prepayment'), Value(0, output_field=DecimalField())),
            output_field=DecimalField()
        )
    ).filter(
        calculated_debt__gt=0
    ).exclude(
        status__in=['BEKOR_QILINDI', 'RAD_ETILDI']
    ).select_related('parent_order')

    # 4. Qidiruv
    if search_query:
        debts = debts.filter(
            Q(order_number__icontains=search_query) |
            Q(customer_name__icontains=search_query) |
            Q(customer_unique_id__icontains=search_query) |
            Q(product_name__icontains=search_query)
        )
    
    # 5. Saralash
    sort_dict = {
        'debt_desc': '-calculated_debt',
        'debt_asc': 'calculated_debt',
        'date_desc': '-created_at',
        'date_asc': 'created_at',
        'name_asc': 'customer_name'
    }
    debts = debts.order_by(sort_dict.get(sort_by, '-calculated_debt'))
    
    # 6. Excel Export - Professional Formatlash bilan
    if request.GET.get('export') == 'excel':
        export_data = []
        for order in debts:
            export_data.append({
                'Buyurtma №': order.order_number,
                'Mijoz nomi': order.customer_name,
                'Mijoz ID': order.customer_unique_id,
                'Mahsulot': order.product_name,
                'Umumiy summa ($)': float(order.total_price or 0),
                'To\'langan ($)': float(order.prepayment or 0),
                'Qoldiq qarz ($)': float(order.calculated_debt),
                'Holat': order.get_status_display(),
                'Sana': order.created_at.strftime('%d.%m.%Y') if order.created_at else '-',
            })
        
        df = pd.DataFrame(export_data)
        output = io.BytesIO()
        writer = pd.ExcelWriter(output, engine='xlsxwriter')
        df.to_excel(writer, index=False, sheet_name='Qarzlar Ro\'yxati')
        
        workbook  = writer.book
        worksheet = writer.sheets['Qarzlar Ro\'yxati']

        # Formatlar
        header_fmt = workbook.add_format({'bold': True, 'bg_color': '#27ae60', 'font_color': 'white', 'border': 1})
        money_fmt = workbook.add_format({'num_format': '#,##0.00', 'border': 1})
        border_fmt = workbook.add_format({'border': 1})

        # Sarlavhalarni formatlash va kenglikni sozlash
        for col_num, value in enumerate(df.columns.values):
            worksheet.write(0, col_num, value, header_fmt)
            column_len = max(df[value].astype(str).str.len().max(), len(value)) + 5
            worksheet.set_column(col_num, col_num, column_len, border_fmt)

        # Pul ustunlariga (E, F, G) format berish
        worksheet.set_column('E:G', 18, money_fmt)
        writer.close()
        output.seek(0)

        response = HttpResponse(output, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename=Ecoprom_Qarzlar_{request.user.username}.xlsx'
        return response

    # 7. Statistika va Paginatsiya
    stats = debts.aggregate(
        total_debt_sum=Sum('calculated_debt'),
        total_rev=Sum('total_price'),
        total_paid_sum=Sum('prepayment')
    )
    
    paginator = Paginator(debts, 20)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    context = {
        'page_obj': page_obj,
        'total_debt': stats['total_debt_sum'] or 0,
        'total_orders_count': debts.count(),
        'total_revenue': stats['total_rev'] or 0,
        'total_paid': stats['total_paid_sum'] or 0,
        'debt_customers_count': debts.values('customer_unique_id').distinct().count(),
        'search_query': search_query,
        'sort_by': sort_by,
        'is_admin': is_admin,
    }
    return render(request, 'orders/debt_report.html', context)

def add_prepayment(request, order_id):
    if request.method == 'POST':
        order = get_object_or_404(Order, id=order_id)
        try:
            amount_str = request.POST.get('amount', '0').replace(',', '.') # Vergulni nuqtaga almashtirish
            amount = float(amount_str)
            
            if amount <= 0:
                messages.error(request, "To'lov summasi 0 dan katta bo'lishi kerak.")
            else:
                # Qarzdan ko'p to'lov kiritilayotganini tekshirish (ixtiyoriy)
                remaining = float(order.total_price) - float(order.prepayment or 0)
                if amount > remaining:
                    messages.warning(request, f"E'tibor bering: Kiritilgan summa qarzdan ({remaining}) ko'proq.")

                # Yangi zalog summasini hisoblash
                order.prepayment = float(order.prepayment or 0) + amount
                order.save()
                
                messages.success(request, f"{order.customer_name} uchun {amount} qo'shildi. Umumiy to'langan: {order.prepayment}")
        
        except ValueError:
            messages.error(request, "Xato: Noto'g'ri raqam kiritildi.")
            
    return redirect('debt_report')


from django.db.models import Sum, Count
from django.shortcuts import render
from .models import Order
from django.db.models import Sum, Count, Max

from django.db.models import Sum, Count, Max, F

from django.shortcuts import render
from django.db.models import Sum, Count, Max, F
from django.http import JsonResponse
from .models import Order

from django.shortcuts import render
from django.db.models import Sum, Count, Max, F, Value, DecimalField, ExpressionWrapper
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from .models import Order

from django.shortcuts import render
from django.db.models import Sum, Count, Max, F, Value, DecimalField, ExpressionWrapper, Q
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from .models import Order

from django.shortcuts import render
from django.db.models import Sum, Count, Max, F, Value, DecimalField, ExpressionWrapper, Q
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from .models import Order

from django.db.models import Q, Sum, Count, Max, Value, DecimalField
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import render
from .models import Order

from django.db.models import (
    F, Sum, Count, Max, Avg, Q, 
    Value, DecimalField, Case, When, FloatField
)
from django.db.models.functions import Coalesce, Round, TruncMonth
from django.http import JsonResponse
import json
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import (
    F, Sum, Count, Max, Min, Avg, Q, 
    Value, DecimalField, Case, When, FloatField, 
    CharField, ExpressionWrapper, DurationField
)
import json
from datetime import datetime, timedelta
from django.shortcuts import render
from django.http import JsonResponse
from django.db.models import (
    F, Sum, Count, Max, Min, Avg, Q, 
    Value, DecimalField, Case, When, FloatField, 
    CharField, ExpressionWrapper, DurationField
)
from django.db.models.functions import Coalesce, TruncMonth
from .models import Order

import json
from django.shortcuts import render
from django.http import JsonResponse
from django.db.models import (
    Sum, Count, Avg, Max, Min, F, Q, 
    Case, When, Value, FloatField, DecimalField, ExpressionWrapper, CharField
)
from django.db.models.functions import Coalesce
from .models import Order
from django.db.models import DecimalField, Value, Sum, Q
from django.db.models.functions import Coalesce
def customer_rating(request):
    """
    To'liq biznes analitika: Mijozlar reytingi, Mahsulotlar tahlili, 
    PIR panellar, Eshiklar va Panel qalinligi statistikasi.
    """
    
    # ======================== 1. AJAX SO'ROVLAR ========================
    customer_id = request.GET.get('get_orders')
    if customer_id:
        orders = Order.objects.filter(
            customer_unique_id=customer_id, 
            parent_order__isnull=True
        ).order_by('-created_at')
        
        orders_list = [{
            'order_number': o.order_number,
            'product_name': o.product_name or "Eshik/Mebel",
            'panel_kvadrat': float(o.panel_kvadrat or 0),
            'status': o.status,
            'total_price': float(o.total_price or 0),
            'prepayment': float(o.prepayment or 0),
            'created_at': o.created_at.strftime('%Y-%m-%d %H:%M') if o.created_at else '',
        } for o in orders]
        
        customer_stats = orders.aggregate(
            total_orders=Count('id'),
            total_amount=Coalesce(Sum('total_price'), Value(0, output_field=DecimalField())),
            total_paid=Coalesce(Sum('prepayment'), Value(0, output_field=DecimalField())),
            total_area=Coalesce(Sum('panel_kvadrat'), Value(0, output_field=DecimalField())),
            avg_order_value=Coalesce(Avg('total_price'), Value(0, output_field=DecimalField()))
        )
        
        return JsonResponse({
            'orders': orders_list,
            'stats': customer_stats,
            'customer_id': customer_id
        })

    # ======================== 2. MIJOZLAR REYTINGI ========================
    ratings_query = Order.objects.filter(parent_order__isnull=True).values('customer_unique_id').annotate(
        display_name=Max('customer_name'),
        order_count=Count('id'),
        first_order_date=Min('created_at'),
        last_order_date=Max('created_at'),
        total_m2=Coalesce(Sum('panel_kvadrat'), Value(0, output_field=DecimalField())),
        total_billed=Coalesce(Sum('total_price'), Value(0, output_field=DecimalField())),
        total_paid=Coalesce(Sum('prepayment'), Value(0, output_field=DecimalField())),
    ).annotate(
        payment_ratio=Case(
            When(total_billed__gt=0, then=100.0 * F('total_paid') / F('total_billed')),
            default=Value(0.0),
            output_field=FloatField()
        ),
        avg_order_value=ExpressionWrapper(F('total_billed') / F('order_count'), output_field=DecimalField())
    ).annotate(
        loyalty_score=Case(
            When(Q(order_count__gt=5) & Q(payment_ratio__gt=80), then=Value('A')),
            When(Q(order_count__gt=2) & Q(payment_ratio__gt=60), then=Value('B')),
            default=Value('C'),
            output_field=CharField()
        )
    )

    m2_ratings = list(ratings_query.order_by('-total_m2')[:15])
    sum_ratings = list(ratings_query.order_by('-total_billed')[:15])  # total_paid emas, total_billed
    order_count_ratings = list(ratings_query.order_by('-order_count')[:10])
    loyal_customers = list(ratings_query.filter(loyalty_score='A').order_by('-total_billed')[:10])

    # ======================== 3. UMUMIY STATISTIKA ========================
    base_aggregate = Order.objects.filter(parent_order__isnull=True).aggregate(
        total_orders=Count('id'),
        total_customers=Count('customer_unique_id', distinct=True),
        total_revenue=Coalesce(Sum('total_price'), Value(0, output_field=DecimalField())),
        total_prepayment=Coalesce(Sum('prepayment'), Value(0, output_field=DecimalField())),
        total_area=Coalesce(Sum('panel_kvadrat'), Value(0, output_field=DecimalField())),
        avg_order_value=Coalesce(Avg('total_price'), Value(0, output_field=DecimalField())),
    )

    overall_stats = base_aggregate
    if overall_stats['total_revenue'] > 0:
        overall_stats['avg_prepayment_ratio'] = (float(overall_stats['total_prepayment']) * 100) / float(overall_stats['total_revenue'])
    else:
        overall_stats['avg_prepayment_ratio'] = 0

    # TO'G'RILANGAN QISM - status nomlarini tekshiring
    completed_orders = Order.objects.filter(
        parent_order__isnull=True, 
        status__in=['BAJARILDI', 'TAYYOR']  # 'completed' emas, 'delivered' emas
    ).count()
    overall_stats['completion_rate'] = (completed_orders * 100 / overall_stats['total_orders']) if overall_stats['total_orders'] > 0 else 0

    # ======================== 4. PANEL QALINLIGI STATISTIKASI ========================
    thickness_stat = Order.objects.filter(
        parent_order__isnull=True
    ).exclude(
        Q(panel_thickness__isnull=True) | Q(panel_thickness='')
    ).values('panel_thickness').annotate(
        count=Count('id'),
        total_area=Coalesce(Sum('panel_kvadrat'), Value(0, output_field=DecimalField())),
        eshik_types=Count('product_name', distinct=True)
    ).order_by('panel_thickness')

    # ======================== 5. PIR PANELLAR TAHLILI ========================
    pir_all = Order.objects.filter(Q(panel_type__icontains='PIR') | Q(product_name__icontains='PIR'))
    
    pir_stats = pir_all.values('panel_type').annotate(
        count=Count('id'),
        total_m2=Coalesce(Sum('panel_kvadrat'), Value(0, output_field=DecimalField())),
        total_revenue=Coalesce(Sum('total_price'), Value(0, output_field=DecimalField()))
    ).order_by('-total_m2')

    pir_details = {
    'total_pir': pir_all.count(),
    'total_area': pir_all.aggregate(
        s=Coalesce(
            Sum('panel_kvadrat'), 
            Value(0, output_field=DecimalField()) # Mana bu yerda output_field qo'shildi
        )
    )['s'],
    'tom_panels': pir_all.filter(Q(panel_subtype__icontains='TOM') | Q(product_name__icontains='TOM')).count(),
    'secret_panels': pir_all.filter(Q(panel_subtype__icontains='SECRET') | Q(product_name__icontains='SECRET')).count(),
    'sovut_panels': pir_all.filter(Q(panel_subtype__icontains='SOVUT') | Q(product_name__icontains='SOVUT')).count(),
}

    # ======================== 6. ESHIKLAR TAHLILI ========================
    eshik_stat = Order.objects.filter(parent_order__isnull=True).exclude(
        Q(eshik_turi__isnull=True) | Q(eshik_turi='')
    ).values('eshik_turi').annotate(
        eshik_soni=Count('id'),
        total_revenue=Coalesce(Sum('total_price'), Value(0, output_field=DecimalField()))
    ).order_by('-eshik_soni')

    # ======================== 7. MASHHUR MAHSULOTLAR ========================
    product_rankings = Order.objects.filter(parent_order__isnull=True).values('product_name').annotate(
        order_count=Count('id'),
        total_m2=Coalesce(Sum('panel_kvadrat'), Value(0, output_field=DecimalField())),
        total_revenue=Coalesce(Sum('total_price'), Value(0, output_field=DecimalField()))
    ).order_by('-order_count')[:15]

    product_rankings_list = list(product_rankings)
    if product_rankings_list:
        max_orders = max(p['order_count'] for p in product_rankings_list)
        for p in product_rankings_list:
            p['popularity_score'] = (p['order_count'] * 100) / max_orders if max_orders > 0 else 0

    # ======================== 8. CONTEXT ========================
    context = {
        'm2_ratings': m2_ratings,
        'sum_ratings': sum_ratings,
        'order_count_ratings': order_count_ratings,
        'loyal_customers': loyal_customers,
        'overall_stats': overall_stats,
        'product_rankings': product_rankings_list,
        'thickness_stat': list(thickness_stat),
        'pir_stats': list(pir_stats),
        'pir_details': pir_details,
        'eshik_stat': list(eshik_stat),
        'json_data': {
            'm2_ratings': json.dumps(m2_ratings, default=str),
            'sum_ratings': json.dumps(sum_ratings, default=str),
        }
    }

    return render(request, 'orders/customer_rating.html', context)
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required

@login_required
def get_customer_orders(request, customer_id):
    orders = Order.objects.filter(customer_unique_id=customer_id).values(
        'order_number', 'product_name', 'panel_kvadrat', 'status', 'created_at'
    ).order_by('-created_at')
    
    return JsonResponse({'orders': list(orders)})



from django.shortcuts import render
from django.http import JsonResponse
import json
from .models import Order, OrderItem # Modellaringiz nomi

def create_order_view(request):
    if request.method == "POST":
        try:
            # Frontenddan kelgan JSON ma'lumotni yuklaymiz
            data = json.loads(request.body)
            items = data.get('items', [])
            customer_name = data.get('customer') # Agar mijoz ismi ham yuborilsa
            
            # 1. Asosiy buyurtmani yaratamiz
            order = Order.objects.create(
                customer_name=customer_name,
                # boshqa kerakli maydonlar
            )
            
            # 2. Jadvalning har bir qatorini bazaga saqlaymiz
            for item in items:
                OrderItem.objects.create(
                    order=order,
                    product_name=item.get('name'),      # Stevnoi, Krovelnie yoki Eshik
                    product_type=item.get('sub_type'),  # F1..F8 yoki Qalinlik (80mm)
                    length=item.get('length') or 0,
                    quantity=item.get('count') or 0,
                    area=item.get('area') or 0,
                    price=item.get('price') or 0,
                    total_sum=item.get('total') or 0
                )
            
            return JsonResponse({"status": "success", "message": "Ma'lumotlar muvaffaqiyatli saqlandi!"})
        
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)

    # Agar GET so'rovi bo'lsa, sahifani o'zini qaytaramiz
    return render(request, 'orders/create_order.html')
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json

@csrf_exempt # Osonlik uchun, lekin haqiqiy loyihada CSRF token yuborgan ma'qul
def save_order_ajax(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            items = data.get('items', [])
            
            # Asosiy buyurtmani yaratish
            order = Order.objects.create(customer_name=data.get('customer', 'Noma\'lum'))
            
            # Har bir qatorni saqlash
            for item in items:
                OrderItem.objects.create(
                    order=order,
                    product_name=item['name'],
                    product_type=item['sub_type'],
                    length=float(item['length'] or 0),
                    quantity=int(item['count'] or 0),
                    area=float(item['area'] or 0),
                    price=float(item['price'] or 0),
                    total_sum=float(item['total'] or 0)
                )
            return JsonResponse({"status": "success"})
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny # Mini App uchun vaqtincha
from .models import Order

class MyOrdersAPIView(APIView):
    # Dastlab tekshirish oson bo'lishi uchun AllowAny qilamiz
    # Keyinchalik xavfsizlikni kuchaytiramiz
    permission_classes = [AllowAny] 

    def get(self, request):
        # Bot orqali keladigan unique_id (Masalan: 656)
        customer_id = request.query_params.get('customer_id') 
        
        if not customer_id:
            return Response({"error": "ID ko'rsatilmadi"}, status=400)
            
        orders = Order.objects.filter(customer_unique_id=customer_id).order_by('-created_at')
        
        data = [
            {
                "order_number": o.order_number,
                "product_name": o.product_name or "Panel/Eshik",
                "status": o.get_status_display(),
                "total_price": float(o.total_price),
                "remaining": float(o.remaining_amount),
                "created_at": o.created_at.strftime('%d.%m.%Y %H:%M'),
            }
            for o in orders
        ]
        return Response(data)

import math  # <--- Mana shu qatorni qo'shing
import math
from decimal import Decimal
import math
from decimal import Decimal

import math
from decimal import Decimal, ROUND_HALF_UP
import math
from decimal import Decimal, ROUND_HALF_UP
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from decimal import Decimal, ROUND_HALF_UP
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .models import Order
from decimal import Decimal, ROUND_HALF_UP
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .models import Order
from decimal import Decimal, ROUND_HALF_UP
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .models import Order
from decimal import Decimal, ROUND_HALF_UP
from django.shortcuts import render
from django.db.models import Sum
from .models import Order

def order_calculator_list(request):
    # Faqat asosiy panellarni olamiz (parent buyurtmalar)
    orders = Order.objects.filter(parent_order__isnull=True).order_by('-id')
    
    # Qalinlik bo'yicha koeffitsientlar lug'ati
    SIRYO_COEFFICIENTS = {
        '5': Decimal('2'),
        '8': Decimal('3'),
        '10': Decimal('4'),
        '15': Decimal('6'),
    }

    calculated_data = []
    total_kvadrat_all = Decimal('0')
    total_siryo_all = Decimal('0')
    total_zamok_all = 0
    total_stakanchik_all = 0

    for order in orders:
        kv = Decimal(str(order.panel_kvadrat or 0))
        # Qalinlikni string ko'rinishida olamiz (masalan: "10")
        thickness = str(order.panel_thickness or "10").strip()
        
        # Siryo hisobi: agar lug'atda qalinlik bo'lsa o'shani, bo'lmasa 10cm koeffitsientini oladi
        coeff = SIRYO_COEFFICIENTS.get(thickness, Decimal('4'))
        siryo_val = kv * coeff
        
        # Boshqa hisoblar
        list_val = kv * Decimal('2') # Har bir qatorda toza kvadrat * 2
        zamok_val = int((kv * Decimal('6')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        stakan_val = int((kv * Decimal('8')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        boyi_val = kv / Decimal('0.96') if kv > 0 else 0

        calculated_data.append({
            'order': order,
            'thickness': thickness,
            'siryo': siryo_val,
            'list': list_val,
            'zamok': zamok_val,
            'stakanchik': stakan_val,
            'boyi': boyi_val,
        })

        # Jami summalar uchun yig'ib boramiz
        total_kvadrat_all += kv
        total_siryo_all += siryo_val
        total_zamok_all += zamok_val
        total_stakanchik_all += stakan_val

    # JAMI LIST: (Jami Kvadrat * 2) + 10 metr zapas
    total_list_final = (total_kvadrat_all * Decimal('2')) + Decimal('10') if total_kvadrat_all > 0 else 0

    context = {
        'data': calculated_data,
        'total_kvadrat_all': total_kvadrat_all,
        'total_list_all': total_list_final,
        'total_siryo_all': total_siryo_all,
        'total_zamok_all': total_zamok_all,
        'total_stakanchik_all': total_stakanchik_all,
    }
    
    return render(request, 'orders/calculator.html', context)
from django.shortcuts import render
from django.db.models import Sum, Count, Q, F
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from .models import Order, Material, MaterialTransaction

def director_dashboard(request):
    now = timezone.now()
    today = now.date()
    
    # ======================== 1. ORDER STATISTIKASI ========================
    total_orders = Order.objects.count()
    
    # Tugatilgan orderlar (BAJARILDI, TAYYOR, USTA_TUGATDI)
    completed_orders = Order.objects.filter(
        status__in=['BAJARILDI', 'TAYYOR', 'USTA_TUGATDI']
    ).count()
    
    # Jarayondagilar (KIRITILDI, TASDIQLANDI, USTA_QABUL_QILDI, USTA_BOSHLA, ISHDA)
    in_progress = Order.objects.filter(
        status__in=['KIRITILDI', 'TASDIQLANDI', 'USTA_QABUL_QILDI', 'USTA_BOSHLA', 'ISHDA']
    ).count()
    
    # Muddati o'tganlar (deadline < hozir va tugatilmagan)
    overdue_orders = Order.objects.filter(
        deadline__lt=now
    ).exclude(
        status__in=['BAJARILDI', 'TAYYOR', 'USTA_TUGATDI', 'RAD_ETILDI']
    ).count()
    
    # Yangi orderlar (oxirgi 7 kun)
    week_ago = now - timedelta(days=7)
    new_orders = Order.objects.filter(created_at__gte=week_ago).count()
    
    # Protsentlar
    completion_rate = round(completed_orders / total_orders * 100, 1) if total_orders else 0
    overdue_rate = round(overdue_orders / total_orders * 100, 1) if total_orders else 0
    in_progress_rate = round(in_progress / total_orders * 100, 1) if total_orders else 0
    
    # ======================== 2. OMBORXONA HOLATI ========================
    total_materials = Material.objects.count()
    
    # Jami ombor qiymati
    warehouse_value = Material.objects.aggregate(
        total=Sum(F('quantity') * F('price_per_unit'))
    )['total'] or Decimal(0)
    
    # Kategoriya bo'yicha (mavjud bo'lsa)
    try:
        from .models import Category
        raw_materials = Material.objects.filter(category__name__icontains='xom').count()
        semi_finished = Material.objects.filter(category__name__icontains='yarim').count()
        finished = Material.objects.filter(category__name__icontains='tayyor').count()
    except:
        raw_materials = total_materials
        semi_finished = 0
        finished = 0
    
    # Kam qolgan materiallar
    low_stock_materials = Material.objects.filter(quantity__lte=F('min_stock_level')).count()
    
    # ======================== 3. UMUMIY TUSHUM ========================
    # Oy boshidan
    monthly_revenue = Order.objects.filter(
        created_at__month=now.month,
        created_at__year=now.year,
        status__in=['BAJARILDI', 'TAYYOR']
    ).aggregate(total=Sum('total_price'))['total'] or Decimal(0)
    
    # Yil boshidan
    yearly_revenue = Order.objects.filter(
        created_at__year=now.year,
        status__in=['BAJARILDI', 'TAYYOR']
    ).aggregate(total=Sum('total_price'))['total'] or Decimal(0)
    
    # So'nggi 30 kun
    last_30_days = now - timedelta(days=30)
    last_30_revenue = Order.objects.filter(
        created_at__gte=last_30_days,
        status__in=['BAJARILDI', 'TAYYOR']
    ).aggregate(total=Sum('total_price'))['total'] or Decimal(0)
    
    # ======================== 4. QARZDORLAR ========================
    debt_orders = Order.objects.filter(
        total_price__gt=F('prepayment')
    ).exclude(status__in=['RAD_ETILDI', 'BEKOR_QILINDI'])
    
    total_debt = sum(order.total_price - order.prepayment for order in debt_orders)
    debt_count = debt_orders.count()
    
    # ======================== 5. OYLIK GRAFIK ========================
    monthly_data = []
    for i in range(5, -1, -1):
        month_date = now - timedelta(days=30*i)
        revenue = Order.objects.filter(
            created_at__month=month_date.month,
            created_at__year=month_date.year,
            status__in=['BAJARILDI', 'TAYYOR']
        ).aggregate(total=Sum('total_price'))['total'] or Decimal(0)
        monthly_data.append({
            'month': month_date.strftime('%b'),
            'revenue': float(revenue) / 1_000_000
        })
    
    # ======================== 6. SO'NGI HARAKATLAR ========================
    recent_transactions = MaterialTransaction.objects.select_related(
        'material', 'performed_by'
    ).order_by('-timestamp')[:10]
    
    # ======================== 7. USTALAR STATISTIKASI ========================
    from .models import Worker
    workers_count = Worker.objects.count()
    
    # ======================== CONTEXT ========================
    context = {
        # Order ma'lumotlari
        'total_orders': total_orders,
        'overdue_orders': overdue_orders,
        'in_progress': in_progress,
        'new_orders': new_orders,
        'completed_orders': completed_orders,
        'completion_rate': completion_rate,
        'overdue_rate': overdue_rate,
        'in_progress_rate': in_progress_rate,
        
        # Ombor ma'lumotlari
        'total_products': total_materials,
        'warehouse_value': warehouse_value,
        'raw_materials': raw_materials,
        'semi_finished': semi_finished,
        'finished': finished,
        'low_stock_materials': low_stock_materials,
        
        # Tushum ma'lumotlari
        'monthly_revenue': monthly_revenue,
        'yearly_revenue': yearly_revenue,
        'last_30_revenue': last_30_revenue,
        'monthly_data': monthly_data,
        
        # Qarz ma'lumotlari
        'total_debt': total_debt,
        'debt_count': debt_count,
        
        # So'nggi harakatlar
        'recent_transactions': recent_transactions,
        
        # Ustalar soni
        'workers_count': workers_count,
        
        # Joriy vaqt
        'now': now,
    }
    
    return render(request, 'orders/director_dashboard.html', context)

import pandas as pd
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from .models import Material, Category
import json
import traceback
import logging

# Logger yaratish
logger = logging.getLogger(__name__)
import pandas as pd
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from .models import Material, Category
import logging
import traceback

logger = logging.getLogger(__name__)

@login_required
@csrf_exempt
def import_excel_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Faqat POST mumkin'}, status=405)

    if 'excel_file' not in request.FILES:
        return JsonResponse({'success': False, 'message': 'Fayl yuklanmadi'}, status=400)

    try:
        excel_file = request.FILES['excel_file']
        
        # MUHIM: Faylingizda 1-qator sarlavha emas, shuning uchun skiprows=[0] yoki header=1 qilamiz
        # Sarlavhalar 2-qatorda (Index 1) joylashgan
        df = pd.read_excel(excel_file, engine='openpyxl', header=1) 
        
        # Ustun nomlaridagi bo'sh joylarni tozalash
        df.columns = [str(c).strip() for c in df.columns]
        
        logger.info(f"Topilgan ustunlar: {df.columns.tolist()}")

        # Agar 'Наименование' ustuni bo'lmasa, sarlavha noto'g'ri o'qilgan
        if 'Наименование' not in df.columns:
            return JsonResponse({
                'success': False, 
                'message': f'Faylda "Наименование" ustuni topilmadi. Mavjud ustunlar: {", ".join(df.columns)}'
            })

        created_count = 0
        updated_count = 0
        errors = []

        for index, row in df.iterrows():
            try:
                name = row.get('Наименование')
                
                # Bo'sh qatorlarni tashlab ketish
                if pd.isna(name) or str(name).strip().lower() in ['nan', '']:
                    continue
                
                name = str(name).strip()

                # Miqdorni aniqlash: Excelingizda 'Количество (шт.)' ustuni bor
                quantity = 0
                qty_val = row.get('Количество (шт.)')
                
                # Agar 'Количество (шт.)' bo'sh bo'lsa, 'Масса (кг)' ni tekshirish
                if pd.isna(qty_val) or qty_val == '':
                    qty_val = row.get('Масса (кг)', 0)

                try:
                    quantity = float(qty_val) if pd.notna(qty_val) else 0
                except:
                    quantity = 0

                # Kategoriya aniqlash
                category_name = detect_category(name)
                category, _ = Category.objects.get_or_create(name=category_name)

                # O'lchov birligini aniqlash
                unit = 'dona'
                if 'Масса (кг)' in row and pd.notna(row['Масса (кг)']) and not pd.notna(row['Количество (шт.)']):
                    unit = 'kg'
                elif any(x in name.lower() for x in ['болт', 'винт', 'припой', 'масло']):
                    unit = 'kg'

                # Materialni saqlash
                material, created = Material.objects.update_or_create(
                    name=name,
                    defaults={
                        'category': category,
                        'unit': unit,
                        'min_stock_level': 0
                    }
                )
                
                if created:
                    material.quantity = quantity
                    created_count += 1
                else:
                    material.quantity += quantity
                    updated_count += 1
                
                material.save()

            except Exception as row_e:
                errors.append(f"Qator {index + 3}: {str(row_e)}")

        return JsonResponse({
            'success': True,
            'created': created_count,
            'updated': updated_count,
            'errors': errors[:10]
        })

    except Exception as e:
        logger.error(traceback.format_exc())
        return JsonResponse({'success': False, 'message': f"Xato: {str(e)}"}, status=500)

def detect_category(name):
    """Material nomiga qarab kategoriyani aniqlash"""
    name_lower = name.lower()
    
    if 'трв' in name_lower:
        return 'ТРВ'
    elif 'отвод' in name_lower:
        return 'Отводы'
    elif 'медная труб' in name_lower:
        return 'Медные трубы'
    elif 'фреон' in name_lower:
        return 'Фреоны'
    elif 'щит' in name_lower:
        return 'Щиты'
    elif 'фильтр' in name_lower:
        return 'Фильтры'
    elif 'вибрашланг' in name_lower:
        return 'Вибрашланги'
    elif 'магнитный клапан' in name_lower:
        return 'Магнитные клапаны'
    elif 'манометр' in name_lower:
        return 'Манометры'
    elif 'реле' in name_lower:
        return 'Реле'
    elif 'термо' in name_lower and 'контроллер' in name_lower:
        return 'Термоконтроллеры'
    elif 'масло' in name_lower:
        return 'Масла'
    elif 'конденсатор' in name_lower:
        return 'Конденсаторы'
    elif 'глазок' in name_lower:
        return 'Глазки'
    elif 'сепаратор' in name_lower or 'жидкостный отделитель' in name_lower:
        return 'Сепараторы'
    elif 'вентилятор' in name_lower:
        return 'Вентиляторы'
    elif 'болт' in name_lower or 'винт' in name_lower:
        return 'Крепеж'
    elif 'припой' in name_lower:
        return 'Припои'
    elif 'компрессор' in name_lower:
        return 'Компрессоры'
    elif 'заклепка' in name_lower:
        return 'Заклепки'
    elif 'скотч' in name_lower:
        return 'Скотчи'
    elif 'пена' in name_lower:
        return 'Пена'
    elif 'стакан' in name_lower:
        return 'Стаканы'
    elif 'стрейч' in name_lower or 'ekoprom' in name_lower:
        return 'Стрейч-пленка'
    elif name.startswith('F1') or name.startswith('F5') or name.startswith('F8') or name.startswith('F9'):
        return 'Дверные комплектующие'
    elif 'сверло' in name_lower or 'метчик' in name_lower:
        return 'Инструменты'
    elif 'игла' in name_lower:
        return 'Иглы'
    elif 'штуцер' in name_lower:
        return 'Штуцеры'
    elif 'краник' in name_lower:
        return 'Краники'
    else:
        return 'Разное'
from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from .models import Order
import json

@login_required
def chamber_drawing(request, pk):
    """Texnik chizma sahifasi"""
    order = get_object_or_404(Order, pk=pk)
    
    # Spetsifikatsiyani olish
    spec = order.technical_spec_json or {}
    
    context = {
        'order': order,
        'spec': spec,
    }
    return render(request, 'orders/chamber_drawing.html', context)


@login_required
def download_drawing_svg(request, pk):
    """SVG faylni yuklash"""
    order = get_object_or_404(Order, pk=pk)
    
    if order.technical_drawing_svg:
        response = HttpResponse(order.technical_drawing_svg.read(), content_type='image/svg+xml')
        response['Content-Disposition'] = f'attachment; filename="{order.order_number}_drawing.svg"'
        return response
    
    return JsonResponse({'error': 'Fayl topilmadi'}, status=404)


@login_required
def download_drawing_pdf(request, pk):
    """PDF faylni yuklash"""
    import weasyprint
    from django.template.loader import render_to_string
    
    order = get_object_or_404(Order, pk=pk)
    
    html_string = render_to_string('orders/chamber_drawing_pdf.html', {
        'order': order,
        'spec': order.technical_spec_json or {}
    })
    
    pdf = weasyprint.HTML(string=html_string).write_pdf()
    
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{order.order_number}_drawing.pdf"'
    return response


@login_required
def download_spec_json(request, pk):
    """JSON spetsifikatsiyani yuklash"""
    order = get_object_or_404(Order, pk=pk)
    
    response = HttpResponse(
        json.dumps(order.technical_spec_json, indent=2, ensure_ascii=False),
        content_type='application/json'
    )
    response['Content-Disposition'] = f'attachment; filename="{order.order_number}_spec.json"'
    return response


@csrf_exempt
@login_required
def generate_drawing(request, pk):
    """Chizma yaratish"""
    from .signals import generate_chamber_drawing
    
    order = get_object_or_404(Order, pk=pk)
    
    try:
        result = generate_chamber_drawing(order)
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})
    
# constructor/views.py
import json
import math
import os
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Tuple

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import models
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.csrf import csrf_exempt

from .forms import ProjectForm, QuickCalculatorForm
from .models import Project

# =========================================================
# YORDAMCHI FUNKSIYALAR
# =========================================================

def mm_val(s: str) -> int:
    """'100mm' -> 100"""
    return int(str(s).replace("mm", "").strip())


def m_to_mm(m: float) -> int:
    """Metrni millimetrga o'tkazish"""
    return int(round(m * 1000))


def door_dimensions(door_type: str) -> Tuple[int, int]:
    """Eshik o'lchamlarini qaytaradi (en, bo'y) mm da"""
    door_map = {
        "Bir tabaqali (90x190)": (900, 1900),
        "Surilma (120x200)": (1200, 2000),
        "Muzlatkich eshigi": (960, 2000),
        "Yo'q": (0, 0),
    }
    return door_map.get(door_type, (0, 0))


def panel_count_linear(length_m: float, panel_width_m: float = 1.16) -> Dict:
    """Berilgan uzunlik uchun panel sonini hisoblash"""
    full = int(length_m // panel_width_m)
    rem = round(length_m - (full * panel_width_m), 3)
    total = full + (1 if rem > 0.01 else 0)
    return {
        "full_panels": full,
        "remainder_m": rem,
        "total_panels": total
    }


def split_center_by_960(center_mm: int, module_mm: int = 960) -> List[int]:
    """Markaziy qismni 960mm modullarga bo'lish"""
    if center_mm <= 0:
        return []
    
    parts = []
    remain = center_mm
    
    while remain > module_mm:
        next_remain = remain - module_mm
        if next_remain <= module_mm:
            parts.append(module_mm)
            remain = next_remain
            break
        parts.append(module_mm)
        remain -= module_mm
    
    if remain > 0:
        parts.append(remain)
    
    return parts


def build_side_segments(total_mm: int, corner_mm: int = 480, module_mm: int = 960) -> List[int]:
    """Devor tomonini segmentlarga bo'lish (burchak 480mm + markaziy modullar)"""
    if total_mm <= 0:
        return []
    if total_mm <= corner_mm * 2:
        return [total_mm]
    
    center_mm = total_mm - (corner_mm * 2)
    center_parts = split_center_by_960(center_mm, module_mm)
    return [corner_mm] + center_parts + [corner_mm]


def segment_meta(parts: List[int], has_door: bool = False, door_size: int = 960) -> List[Dict]:
    """Segmentlarni tiplar bilan qaytarish (panel yoki eshik)"""
    result = []
    door_used = False
    
    for p in parts:
        if has_door and (not door_used) and p == door_size:
            result.append({"size": p, "type": "door"})
            door_used = True
        else:
            result.append({"size": p, "type": "panel"})
    
    return result


def calculate_all(project) -> Dict:
    """Loyiha bo'yicha barcha hisob-kitoblarni bajarish"""
    
    L = float(project.length_m)
    W = float(project.width_m)
    H = float(project.height_m)
    
    wall_mm = mm_val(project.wall_thickness)
    ceil_mm = mm_val(project.ceiling_thickness)
    floor_mm = mm_val(project.floor_thickness) if project.has_floor else 0
    
    panel_width = float(project.panel_width)
    door_w_mm, door_h_mm = door_dimensions(project.door_type)
    
    # Hajmlar
    hajm = round(L * W * H, 2)
    inner_L_mm = max(0, m_to_mm(L) - (2 * wall_mm))
    inner_W_mm = max(0, m_to_mm(W) - (2 * wall_mm))
    inner_H_mm = max(0, m_to_mm(H) - ceil_mm - floor_mm)
    inner_hajm = round((inner_L_mm * inner_W_mm * inner_H_mm) / 1_000_000_000, 2)
    
    # Maydonlar
    s_devor = round(2 * (L + W) * H, 2)
    s_patalok = round(L * W, 2)
    s_pol = round(L * W, 2) if project.has_floor else 0
    total_panel_area = round(s_devor + s_patalok + s_pol, 2)
    
    # Panel sonlari
    wall_layout_L = panel_count_linear(L, panel_width)
    wall_layout_W = panel_count_linear(W, panel_width)
    
    devor_panels_total = (wall_layout_L["total_panels"] * 2) + (wall_layout_W["total_panels"] * 2)
    patalok_panels_total = math.ceil(W / panel_width)
    pol_panels_total = math.ceil(W / panel_width) if project.has_floor else 0
    estimated_all_panels = devor_panels_total + patalok_panels_total + pol_panels_total
    
    # Segmentlar
    top_parts = build_side_segments(m_to_mm(L))
    right_parts = build_side_segments(m_to_mm(W))
    
    has_door_top = project.door_type != "Yo'q" and project.door_side in ["Old", "Orqa"]
    has_door_right = project.door_type != "Yo'q" and project.door_side in ["Chap", "O'ng"]
    
    top_meta = segment_meta(top_parts, has_door=has_door_top, door_size=door_w_mm)
    right_meta = segment_meta(right_parts, has_door=has_door_right, door_size=door_w_mm)
    
    # Eshik offset
    door_offset_mm = 0
    if project.door_type != "Yo'q":
        if project.door_side in ["Chap", "O'ng"]:
            door_offset_mm = get_door_offset(right_parts, project.door_position, "vertical", door_h_mm)
        else:
            door_offset_mm = get_door_offset(top_parts, project.door_position, "horizontal", door_w_mm)
    
    return {
        "outer_volume_m3": hajm,
        "inner_volume_m3": inner_hajm,
        "inner_L_mm": inner_L_mm,
        "inner_W_mm": inner_W_mm,
        "inner_H_mm": inner_H_mm,
        "wall_area_m2": s_devor,
        "ceiling_area_m2": s_patalok,
        "floor_area_m2": s_pol,
        "total_panel_area_m2": total_panel_area,
        "wall_panels_total": devor_panels_total,
        "ceiling_panels_total": patalok_panels_total,
        "floor_panels_total": pol_panels_total,
        "total_panels_estimated": estimated_all_panels,
        "top_segments": top_parts,
        "right_segments": right_parts,
        "top_meta": top_meta,
        "right_meta": right_meta,
        "wall_mm": wall_mm,
        "ceil_mm": ceil_mm,
        "floor_mm": floor_mm,
        "door_w_mm": door_w_mm,
        "door_h_mm": door_h_mm,
        "door_offset_mm": door_offset_mm,
        "wall_layout_L": wall_layout_L,
        "wall_layout_W": wall_layout_W,
    }


def get_door_offset(parts: List[int], position: str, side_type: str, door_size_mm: int) -> int:
    """Eshikning aniq joylashuv offsetini hisoblash (mm)"""
    total = sum(parts)
    if total <= 0:
        return 0
    
    if side_type == "vertical":  # Chap/O'ng tomon
        if position == "Tepa":
            offset = 480
        elif position == "Past":
            offset = total - 480 - door_size_mm
        else:  # O'rta
            offset = (total - door_size_mm) / 2
    else:  # Old/Orqa tomon (gorizontal)
        if position == "Chap":
            offset = 480
        elif position == "O'ng":
            offset = total - 480 - door_size_mm
        else:  # O'rta
            offset = (total - door_size_mm) / 2
    
    return int(max(0, min(offset, total - door_size_mm)))


def generate_svg(project, calculations):
    """Loyiha uchun texnik chizma SVG yaratish"""
    
    L = float(project.length_m)
    W = float(project.width_m)
    H = float(project.height_m)
    
    wall_mm = calculations['wall_mm']
    ceil_mm = calculations['ceil_mm']
    floor_mm = calculations['floor_mm']
    
    outer_w_mm = m_to_mm(L)
    outer_h_mm = m_to_mm(W)
    outer_z_mm = m_to_mm(H)
    
    top_meta = calculations['top_meta']
    right_meta = calculations['right_meta']
    
    door_w_mm = calculations['door_w_mm']
    door_h_mm = calculations['door_h_mm']
    door_offset_mm = calculations['door_offset_mm']
    
    # Masshtab
    max_draw_w = 250
    max_draw_h = 185
    scale = min(max_draw_w / outer_w_mm, max_draw_h / outer_h_mm)
    
    draw_w = outer_w_mm * scale
    draw_h = outer_h_mm * scale
    wall_t = max(6, wall_mm * scale)
    
    svg_w = 800
    svg_h = 600
    
    x_center = 400
    px = x_center - draw_w / 2
    py = 100
    
    inner_L_mm = calculations['inner_L_mm']
    inner_W_mm = calculations['inner_W_mm']
    inner_H_mm = calculations['inner_H_mm']
    
    # Ranglar
    c = {
        "sheet": "#ffffff",
        "line": "#111111",
        "dim": "#222222",
        "text": "#111111",
        "muted": "#555555",
        "door": "#111111",
    }
    
    date_str = datetime.now().strftime("%d.%m.%Y")
    
    # ========== SVG YIG'ISH ==========
    svg_parts = []
    
    # Header
    svg_parts.append(f'<svg width="100%" viewBox="0 0 {svg_w} {svg_h}" xmlns="http://www.w3.org/2000/svg">')
    svg_parts.append(f'<rect x="10" y="10" width="{svg_w-20}" height="{svg_h-20}" fill="{c["sheet"]}" stroke="none"/>')
    
    svg_parts.append(f'<text x="{x_center}" y="40" font-size="16" font-weight="bold" text-anchor="middle" fill="{c["text"]}">TEXNIK CHIZMA</text>')
    svg_parts.append(f'<text x="{x_center}" y="60" font-size="12" font-weight="bold" text-anchor="middle" fill="{c["text"]}">{(project.project_name or "").upper()}</text>')
    svg_parts.append(f'<text x="{svg_w-20}" y="40" font-size="11" text-anchor="end" fill="{c["muted"]}">{project.room_code}</text>')
    
    # Tashqi va ichki to'rtburchak
    svg_parts.append(f'<rect x="{px}" y="{py}" width="{draw_w}" height="{draw_h}" fill="none" stroke="{c["line"]}" stroke-width="1.6"/>')
    svg_parts.append(f'<rect x="{px+wall_t}" y="{py+wall_t}" width="{draw_w-2*wall_t}" height="{draw_h-2*wall_t}" fill="none" stroke="{c["line"]}" stroke-width="1.1"/>')
    
    # Ichki o'lcham matni
    svg_parts.append(f'<text x="{px+draw_w/2+8}" y="{py+draw_h/2-8}" font-size="14" font-weight="bold" text-anchor="middle" fill="{c["text"]}" transform="rotate(90 {px+draw_w/2+8},{py+draw_h/2-8})">H-{outer_z_mm}</text>')
    svg_parts.append(f'<text x="{px+draw_w/2}" y="{py+draw_h+40}" font-size="10" text-anchor="middle" fill="{c["muted"]}">Ichki: {inner_L_mm} x {inner_W_mm} x {inner_H_mm} mm</text>')
    
    # Uzunlik o'lchami
    svg_parts.append(dim_h(px, px+draw_w, py-15, f"{outer_w_mm} mm", c["dim"]))
    
    # En o'lchami
    svg_parts.append(dim_v(px+draw_w+15, py, py+draw_h, f"{outer_h_mm} mm", c["dim"]))
    
    # Eshik (agar bor bo'lsa)
    if project.door_type != "Yo'q":
        door_svg = draw_door(project, px, py, draw_w, draw_h, scale, door_offset_mm, door_w_mm, door_h_mm, c)
        svg_parts.append(door_svg)
    
    # Segment chiziqlari
    svg_parts.append(chain_dim_top(px, py-6, top_meta, scale, c["dim"]))
    svg_parts.append(chain_dim_right(px+draw_w+8, py, right_meta, scale, c["dim"]))
    
    # Devor qalinligi
    svg_parts.append(f'<text x="{px+draw_w/2}" y="{py+draw_h+20}" font-size="11" text-anchor="middle" fill="{c["text"]}">Devor: {wall_mm} mm | Patalok: {ceil_mm} mm | Pol: {floor_mm if project.has_floor else 0} mm</text>')
    
    # Title block
    svg_parts.append(title_block(px-50, py+draw_h+60, draw_w+100, 80, project, wall_mm, ceil_mm, floor_mm, date_str, c))
    
    svg_parts.append(f'<text x="{svg_w-20}" y="{svg_h-20}" font-size="10" text-anchor="end" fill="{c["muted"]}">EcoProm Konstruktor</text>')
    svg_parts.append('</svg>')
    
    return '\n'.join(svg_parts)


def dim_h(x1, x2, y, text, color="#222"):
    """Gorizontal o'lcham chizig'i"""
    return f"""
    <g stroke="{color}" fill="none" stroke-width="1">
        <line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" />
        <line x1="{x1}" y1="{y-5}" x2="{x1}" y2="{y+5}" />
        <line x1="{x2}" y1="{y-5}" x2="{x2}" y2="{y+5}" />
    </g>
    <text x="{(x1+x2)/2}" y="{y-6}" font-size="10" text-anchor="middle" fill="{color}">{text}</text>
    """


def dim_v(x, y1, y2, text, color="#222"):
    """Vertikal o'lcham chizig'i"""
    return f"""
    <g stroke="{color}" fill="none" stroke-width="1">
        <line x1="{x}" y1="{y1}" x2="{x}" y2="{y2}" />
        <line x1="{x-5}" y1="{y1}" x2="{x+5}" y2="{y1}" />
        <line x1="{x-5}" y1="{y2}" x2="{x+5}" y2="{y2}" />
    </g>
    <text x="{x+12}" y="{(y1+y2)/2}" font-size="10" text-anchor="middle" fill="{color}" transform="rotate(90 {x+12},{(y1+y2)/2})">{text}</text>
    """


def chain_dim_top(x, y, parts, scale, color="#222"):
    """Yuqori segment o'lchamlari"""
    svg = ""
    cur = x
    for p in parts:
        nx = cur + p["size"] * scale
        label = f'{p["size"]} ESHIK' if p["type"] == "door" else str(p["size"])
        svg += f'<line x1="{cur}" y1="{y}" x2="{cur}" y2="{y-6}" stroke="{color}" stroke-width="1"/>'
        svg += f'<text x="{(cur+nx)/2}" y="{y-4}" font-size="9" text-anchor="middle" fill="{color}">{label}</text>'
        cur = nx
    svg += f'<line x1="{cur}" y1="{y}" x2="{cur}" y2="{y-6}" stroke="{color}" stroke-width="1"/>'
    return svg


def chain_dim_right(x, y, parts, scale, color="#222"):
    """O'ng segment o'lchamlari"""
    svg = ""
    cur = y
    for p in parts:
        ny = cur + p["size"] * scale
        label = f'{p["size"]} ESHIK' if p["type"] == "door" else str(p["size"])
        svg += f'<line x1="{x}" y1="{cur}" x2="{x+6}" y2="{cur}" stroke="{color}" stroke-width="1"/>'
        svg += f'<text x="{x+10}" y="{(cur+ny)/2}" font-size="9" text-anchor="middle" fill="{color}" transform="rotate(90 {x+10},{(cur+ny)/2})">{label}</text>'
        cur = ny
    svg += f'<line x1="{x}" y1="{cur}" x2="{x+6}" y2="{cur}" stroke="{color}" stroke-width="1"/>'
    return svg


def draw_door(project, px, py, draw_w, draw_h, scale, offset_mm, door_w_mm, door_h_mm, c):
    """Eshik chizish"""
    door_side = project.door_side
    opening = project.door_opening
    
    if door_side == "Chap":
        top = py + offset_mm * scale
        bot = top + door_h_mm * scale
        if opening == "Ichkariga":
            return f"""
            <line x1="{px}" y1="{top}" x2="{px+20}" y2="{top+30}" stroke="{c["door"]}" stroke-width="1.5"/>
            <line x1="{px}" y1="{bot}" x2="{px+20}" y2="{bot-30}" stroke="{c["door"]}" stroke-width="1.5"/>
            <line x1="{px+20}" y1="{top+30}" x2="{px+20}" y2="{bot-30}" stroke="{c["door"]}" stroke-width="1.5" stroke-dasharray="4,3"/>
            """
        else:
            return f"""
            <line x1="{px}" y1="{top}" x2="{px-20}" y2="{top-30}" stroke="{c["door"]}" stroke-width="1.5"/>
            <line x1="{px}" y1="{bot}" x2="{px-20}" y2="{bot+30}" stroke="{c["door"]}" stroke-width="1.5"/>
            <line x1="{px-20}" y1="{top-30}" x2="{px-20}" y2="{bot+30}" stroke="{c["door"]}" stroke-width="1.5" stroke-dasharray="4,3"/>
            """
    
    elif door_side == "O'ng":
        rx = px + draw_w
        top = py + offset_mm * scale
        bot = top + door_h_mm * scale
        if opening == "Ichkariga":
            return f"""
            <line x1="{rx}" y1="{top}" x2="{rx-20}" y2="{top+30}" stroke="{c["door"]}" stroke-width="1.5"/>
            <line x1="{rx}" y1="{bot}" x2="{rx-20}" y2="{bot-30}" stroke="{c["door"]}" stroke-width="1.5"/>
            <line x1="{rx-20}" y1="{top+30}" x2="{rx-20}" y2="{bot-30}" stroke="{c["door"]}" stroke-width="1.5" stroke-dasharray="4,3"/>
            """
        else:
            return f"""
            <line x1="{rx}" y1="{top}" x2="{rx+20}" y2="{top-30}" stroke="{c["door"]}" stroke-width="1.5"/>
            <line x1="{rx}" y1="{bot}" x2="{rx+20}" y2="{bot+30}" stroke="{c["door"]}" stroke-width="1.5"/>
            <line x1="{rx+20}" y1="{top-30}" x2="{rx+20}" y2="{bot+30}" stroke="{c["door"]}" stroke-width="1.5" stroke-dasharray="4,3"/>
            """
    
    elif door_side == "Old":
        left = px + offset_mm * scale
        right = left + door_w_mm * scale
        by = py + draw_h
        if opening == "Ichkariga":
            return f"""
            <line x1="{left}" y1="{by}" x2="{left+30}" y2="{by-20}" stroke="{c["door"]}" stroke-width="1.5"/>
            <line x1="{right}" y1="{by}" x2="{right-30}" y2="{by-20}" stroke="{c["door"]}" stroke-width="1.5"/>
            <line x1="{left+30}" y1="{by-20}" x2="{right-30}" y2="{by-20}" stroke="{c["door"]}" stroke-width="1.5" stroke-dasharray="4,3"/>
            """
        else:
            return f"""
            <line x1="{left}" y1="{by}" x2="{left-30}" y2="{by+20}" stroke="{c["door"]}" stroke-width="1.5"/>
            <line x1="{right}" y1="{by}" x2="{right+30}" y2="{by+20}" stroke="{c["door"]}" stroke-width="1.5"/>
            <line x1="{left-30}" y1="{by+20}" x2="{right+30}" y2="{by+20}" stroke="{c["door"]}" stroke-width="1.5" stroke-dasharray="4,3"/>
            """
    
    elif door_side == "Orqa":
        left = px + offset_mm * scale
        right = left + door_w_mm * scale
        if opening == "Ichkariga":
            return f"""
            <line x1="{left}" y1="{py}" x2="{left+30}" y2="{py+20}" stroke="{c["door"]}" stroke-width="1.5"/>
            <line x1="{right}" y1="{py}" x2="{right-30}" y2="{py+20}" stroke="{c["door"]}" stroke-width="1.5"/>
            <line x1="{left+30}" y1="{py+20}" x2="{right-30}" y2="{py+20}" stroke="{c["door"]}" stroke-width="1.5" stroke-dasharray="4,3"/>
            """
        else:
            return f"""
            <line x1="{left}" y1="{py}" x2="{left-30}" y2="{py-20}" stroke="{c["door"]}" stroke-width="1.5"/>
            <line x1="{right}" y1="{py}" x2="{right+30}" y2="{py-20}" stroke="{c["door"]}" stroke-width="1.5"/>
            <line x1="{left-30}" y1="{py-20}" x2="{right+30}" y2="{py-20}" stroke="{c["door"]}" stroke-width="1.5" stroke-dasharray="4,3"/>
            """
    
    return ""


def title_block(x, y, w, h, project, wall_mm, ceil_mm, floor_mm, date_str, c):
    """Sarlavha bloki"""
    L_mm = m_to_mm(float(project.length_m))
    W_mm = m_to_mm(float(project.width_m))
    H_mm = m_to_mm(float(project.height_m))
    
    return f"""
    <g>
        <rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="{c["line"]}" stroke-width="1"/>
        <line x1="{x}" y1="{y+25}" x2="{x+w}" y2="{y+25}" stroke="{c["line"]}" stroke-width="1"/>
        
        <text x="{x+10}" y="{y+18}" font-size="11" font-weight="bold" fill="{c["text"]}">ECOPROM TECHNICAL DRAWING</text>
        
        <text x="{x+10}" y="{y+45}" font-size="10" fill="{c["muted"]}">Loyiha:</text>
        <text x="{x+60}" y="{y+45}" font-size="10" font-weight="bold" fill="{c["text"]}">{project.project_name or "-"}</text>
        
        <text x="{x+10}" y="{y+65}" font-size="10" fill="{c["muted"]}">O'lcham:</text>
        <text x="{x+60}" y="{y+65}" font-size="10" fill="{c["text"]}">{L_mm} x {W_mm} x {H_mm} mm</text>
        
        <text x="{x+250}" y="{y+45}" font-size="10" fill="{c["muted"]}">Kod:</text>
        <text x="{x+280}" y="{y+45}" font-size="10" font-weight="bold" fill="{c["text"]}">{project.room_code}</text>
        
        <text x="{x+250}" y="{y+65}" font-size="10" fill="{c["muted"]}">Devor/Patalok/Pol:</text>
        <text x="{x+360}" y="{y+65}" font-size="10" fill="{c["text"]}">{wall_mm}/{ceil_mm}/{floor_mm} mm</text>
        
        <text x="{x+w-10}" y="{y+45}" font-size="10" text-anchor="end" fill="{c["muted"]}">Sana: {date_str}</text>
        <text x="{x+w-10}" y="{y+65}" font-size="10" text-anchor="end" fill="{c["muted"]}">Sheet: 1/1</text>
    </g>
    """


def get_ai_recommendation(project):
    """Groq API orqali AI tavsiya olish"""
    api_key = getattr(settings, 'GROQ_API_KEY', os.getenv('GROQ_API_KEY', ''))
    
    if not api_key:
        return {"success": False, "message": "GROQ_API_KEY topilmadi"}
    
    prompt = f"""
Siz sovutish kamerasi bo'yicha professional muhandissiz.
Foydalanuvchiga texnik tavsiya bering.

Mijoz ma'lumotlari:
- Mahsulot turi: {project.product_type}
- Talab qilinadigan harorat: {project.storage_temp}
- Kunlik eshik ochilish soni: {project.opening_freq}
- Hudud / iqlim: {project.region}
- Namlik talabi: {project.humidity}
- O'lcham: {project.length_m}m x {project.width_m}m x {project.height_m}m
- Pol paneli: {"Ha" if project.has_floor else "Yo'q"}

JSON formatda qaytaring:
{{
  "rejim": "...",
  "devor_qalinligi_mm": 100,
  "patalok_qalinligi_mm": 80,
  "pol_qalinligi_mm": 100,
  "agregat_turi": "...",
  "eshik_turi": "...",
  "izoh": "...",
  "xulosa": "..."
}}

Faqat JSON qaytaring.
"""
    
    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "temperature": 0.3,
                "messages": [
                    {"role": "system", "content": "Siz texnik sovutish kamerasi mutaxassisisiz."},
                    {"role": "user", "content": prompt}
                ]
            },
            timeout=60
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1:
            return {"success": False, "message": f"JSON parse bo'lmadi"}
        
        parsed = json.loads(content[start:end+1])
        return {"success": True, "data": parsed}
    
    except Exception as e:
        return {"success": False, "message": f"Groq xatolik: {e}"}


def send_to_telegram(project, calculations):
    """Telegram kanalga hisobot yuborish"""
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', os.getenv('TELEGRAM_BOT_TOKEN', ''))
    chat_id = getattr(settings, 'TELEGRAM_CHAT_ID', os.getenv('TELEGRAM_CHAT_ID', '-1002338157363'))
    
    if not token:
        return False, "TELEGRAM_BOT_TOKEN topilmadi"
    
    # Hisobot matnini tayyorlash
    top_report = " + ".join([
        f"{p['size']} ESHIK" if p["type"] == "door" else str(p["size"]) 
        for p in calculations['top_meta']
    ])
    right_report = " + ".join([
        f"{p['size']} ESHIK" if p["type"] == "door" else str(p["size"]) 
        for p in calculations['right_meta']
    ])
    
    message = f"""
<b>🏗 Yangi buyurtma / loyiha</b>

<b>Loyiha:</b> {project.project_name or '-'}
<b>Kod:</b> {project.room_code}
<b>Sana:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}

<b>Tashqi o'lcham:</b> {project.length_m} × {project.width_m} × {project.height_m} m
<b>Ichki foydali o'lcham:</b> {calculations['inner_L_mm']} × {calculations['inner_W_mm']} × {calculations['inner_H_mm']} mm
<b>Hajm:</b> {calculations['outer_volume_m3']} m³

<b>Devor:</b> {project.wall_type} / {calculations['wall_mm']} mm / {calculations['wall_area_m2']} m²
<b>Patalok:</b> {project.ceiling_type} / {calculations['ceil_mm']} mm / {calculations['ceiling_area_m2']} m²
<b>Pol:</b> {project.floor_type if project.has_floor else 'Mavjud emas'} / {calculations['floor_mm'] if project.has_floor else 0} mm

<b>Eshik:</b> {project.door_type}
<b>Eshik joylashuvi:</b> {project.door_side} / {project.door_position} / {project.door_opening}

<b>Agregat:</b> {project.unit_type} ({project.unit_brand}) / {project.unit_side}

<b>Panel ishchi eni:</b> {project.panel_width} m
<b>Umumiy devor paneli:</b> {calculations['wall_panels_total']} ta
<b>Patalok paneli:</b> {calculations['ceiling_panels_total']} ta
<b>Pol paneli:</b> {calculations['floor_panels_total']} ta

<b>Segmentlar (Uzunlik):</b> {top_report}
<b>Segmentlar (En):</b> {right_report}
""".strip()
    
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30
        )
        if response.status_code != 200:
            return False, f"Telegram xatolik: {response.text}"
        return True, "Yuborildi"
    except Exception as e:
        return False, f"Xatolik: {e}"


# =========================================================
# VIEWS
# =========================================================

@login_required
def constructor_index(request):
    """Konstruktor asosiy sahifasi"""
    
    # So'nggi loyihalar
    recent_projects = Project.objects.all().order_by('-created_at')[:10]
    
    # Tez kalkulyator formasi
    form = QuickCalculatorForm(request.GET or None)
    
    context = {
        'form': form,
        'recent_projects': recent_projects,
        'title': 'Sovutish Kamerasi Konstruktori',
    }
    
    return render(request, 'orders/chizma.html', context)


@login_required
def project_create(request):
    """Yangi loyiha yaratish"""
    
    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.created_by = request.user
            
            # Hisob-kitoblarni bajarish
            calculations = calculate_all(project)
            project.calculations = calculations
            project.save()
            
            messages.success(request, f"✅ Loyiha '{project.room_code}' muvaffaqiyatli yaratildi!")
            return redirect('constructor:project_detail', pk=project.pk)
    else:
        form = ProjectForm()
    
    context = {
        'form': form,
        'title': 'Yangi Loyiha Yaratish',
    }
    
    return render(request, 'constructor/project_form.html', context)


@login_required
def project_detail(request, pk):
    """Loyiha tafsilotlari"""
    
    project = get_object_or_404(Project, pk=pk)
    
    # Hisob-kitoblar (agar mavjud bo'lmasa, qayta hisoblash)
    if not project.calculations:
        calculations = calculate_all(project)
        project.calculations = calculations
        project.save()
    else:
        calculations = project.calculations
    
    # SVG yaratish
    svg_content = generate_svg(project, calculations)
    
    context = {
        'project': project,
        'calculations': calculations,
        'svg_content': svg_content,
        'title': f'Loyiha: {project.room_code}',
    }
    
    return render(request, 'constructor/project_detail.html', context)


@login_required
def project_edit(request, pk):
    """Loyihani tahrirlash"""
    
    project = get_object_or_404(Project, pk=pk)
    
    if request.method == 'POST':
        form = ProjectForm(request.POST, instance=project)
        if form.is_valid():
            project = form.save()
            
            # Hisob-kitoblarni qayta bajarish
            calculations = calculate_all(project)
            project.calculations = calculations
            project.save()
            
            messages.success(request, f"✅ Loyiha '{project.room_code}' yangilandi!")
            return redirect('constructor:project_detail', pk=project.pk)
    else:
        form = ProjectForm(instance=project)
    
    context = {
        'form': form,
        'project': project,
        'title': f'Loyihani tahrirlash: {project.room_code}',
    }
    
    return render(request, 'constructor/project_form.html', context)


@login_required
def project_delete(request, pk):
    """Loyihani o'chirish"""
    
    project = get_object_or_404(Project, pk=pk)
    
    if request.method == 'POST':
        room_code = project.room_code
        project.delete()
        messages.success(request, f"✅ Loyiha '{room_code}' o'chirildi!")
        return redirect('constructor:project_list')
    
    context = {
        'project': project,
        'title': f'Loyihani o\'chirish: {project.room_code}',
    }
    
    return render(request, 'constructor/project_confirm_delete.html', context)


@login_required
def project_list(request):
    """Barcha loyihalar ro'yxati"""
    
    projects = Project.objects.all().order_by('-created_at')
    
    # Filtrlar
    search = request.GET.get('search', '')
    if search:
        projects = projects.filter(
            models.Q(project_name__icontains=search) |
            models.Q(room_code__icontains=search)
        )
    
    # Pagination
    paginator = Paginator(projects, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'search': search,
        'title': 'Barcha Loyihalar',
    }
    
    return render(request, 'constructor/project_list.html', context)


@login_required
def ai_recommendation(request, pk):
    """AI tavsiya olish"""
    
    project = get_object_or_404(Project, pk=pk)
    
    result = get_ai_recommendation(project)
    
    if result['success']:
        project.ai_result = result['data']
        project.save()
        messages.success(request, "✅ AI tavsiya muvaffaqiyatli olindi!")
    else:
        messages.error(request, f"❌ AI tavsiya olinmadi: {result['message']}")
    
    return redirect('constructor:project_detail', pk=project.pk)


@login_required
def send_report(request, pk):
    """Telegramga hisobot yuborish"""
    
    project = get_object_or_404(Project, pk=pk)
    
    if not project.calculations:
        calculations = calculate_all(project)
        project.calculations = calculations
        project.save()
    else:
        calculations = project.calculations
    
    success, message = send_to_telegram(project, calculations)
    
    if success:
        messages.success(request, "✅ Hisobot Telegram kanalga yuborildi!")
    else:
        messages.error(request, f"❌ Yuborilmadi: {message}")
    
    return redirect('constructor:project_detail', pk=project.pk)


@login_required
def create_order_from_project(request, pk):
    """Loyihadan buyurtma yaratish (orders app ga o'tkazish)"""
    
    project = get_object_or_404(Project, pk=pk)
    
    if not project.calculations:
        calculations = calculate_all(project)
        project.calculations = calculations
        project.save()
    else:
        calculations = project.calculations
    
    # Order modelini import qilish (orders app dan)
    try:
        from orders.models import Order
        
        order = Order.objects.create(
            order_number=f"ORD-{datetime.now().strftime('%Y%m%d')}-{Order.objects.count()+1:04d}",
            customer_name=project.project_name or "Noma'lum",
            customer_unique_id=project.room_code,
            product_name=f"Sovutish kamerasi {project.length_m}x{project.width_m}x{project.height_m}m ({project.wall_type})",
            panel_kvadrat=Decimal(str(calculations['total_panel_area_m2'])),
            panel_thickness=str(calculations['wall_mm']),
            total_price=Decimal('0'),  # Narxni keyinroq kiritish mumkin
            prepayment=Decimal('0'),
            status='KIRITILDI',
            created_by=request.user,
            comment=f"Konstruktor orqali yaratilgan. Loyiha ID: {project.id}",
        )
        
        messages.success(request, f"✅ Buyurtma #{order.order_number} muvaffaqiyatli yaratildi!")
        return redirect('order_detail', pk=order.pk)
        
    except ImportError:
        messages.error(request, "❌ Orders app topilmadi yoki ulanishda xatolik!")
    except Exception as e:
        messages.error(request, f"❌ Xatolik: {str(e)}")
    
    return redirect('constructor:project_detail', pk=project.pk)


# =========================================================
# API ENDPOINTLAR (AJAX uchun)
# =========================================================

@login_required
def api_calculate(request):
    """AJAX orqali hisob-kitob qilish"""
    
    if request.method != 'POST':
        return JsonResponse({'error': 'Faqat POST so\'rovi'}, status=405)
    
    try:
        data = json.loads(request.body)
        
        # Vaqtinchalik project obyekti yaratish
        class TempProject:
            def __init__(self, data):
                self.length_m = float(data.get('length', 5))
                self.width_m = float(data.get('width', 4))
                self.height_m = float(data.get('height', 3))
                self.wall_thickness = data.get('wall_thickness', '100mm')
                self.ceiling_thickness = data.get('ceiling_thickness', '80mm')
                self.floor_thickness = data.get('floor_thickness', '100mm')
                self.has_floor = data.get('has_floor', True)
                self.panel_width = float(data.get('panel_width', 1.16))
                self.door_type = data.get('door_type', 'Muzlatkich eshigi')
                self.door_side = data.get('door_side', 'Old')
                self.door_position = data.get('door_position', 'O\'rta')
                self.door_opening = data.get('door_opening', 'Ichkariga')
                self.unit_type = data.get('unit_type', 'Split-sistema (Nizkotemp)')
                self.unit_side = data.get('unit_side', 'Old')
                self.unit_brand = data.get('unit_brand', 'Bitzer')
                self.project_name = data.get('project_name', '')
                self.room_code = data.get('room_code', 'EP-001')
                self.product_type = data.get('product_type', 'Go\'sht')
                self.storage_temp = data.get('storage_temp', '-18°C')
                self.opening_freq = data.get('opening_freq', 'Kam')
                self.region = data.get('region', 'Mo\'tadil')
                self.humidity = data.get('humidity', 'Standart')
        
        project = TempProject(data)
        calculations = calculate_all(project)
        
        return JsonResponse({
            'success': True,
            'calculations': calculations
        })
        
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)

# orders/views.py - api_generate_svg funksiyasi

@login_required
def api_generate_svg(request):
    """AJAX orqali SVG yaratish"""
    
    if request.method != 'POST':
        return JsonResponse({'error': 'Faqat POST so\'rovi'}, status=405)
    
    try:
        data = json.loads(request.body)
        print("API generate-svg received:", data.keys())  # DEBUG
        
        # Vaqtinchalik project yaratish
        class TempProject:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)
        
        # Kerakli maydonlarni olish
        project = TempProject(
            length_m=float(data.get('length', 5)),
            width_m=float(data.get('width', 4)),
            height_m=float(data.get('height', 3)),
            wall_thickness=data.get('wall_thickness', '100mm'),
            ceiling_thickness=data.get('ceiling_thickness', '80mm'),
            floor_thickness=data.get('floor_thickness', '100mm'),
            has_floor=data.get('has_floor', True),
            panel_width=float(data.get('panel_width', 1.16)),
            door_type=data.get('door_type', 'Muzlatkich eshigi'),
            door_side=data.get('door_side', 'Old'),
            door_position=data.get('door_position', 'O\'rta'),
            door_opening=data.get('door_opening', 'Ichkariga'),
            project_name=data.get('project_name', ''),
            room_code=data.get('room_code', 'EP-001'),
        )
        
        # Hisob-kitoblar (agar data da calculations bo'lmasa)
        if 'calculations' in data and data['calculations']:
            calculations = data['calculations']
        else:
            calculations = calculate_all(project)
        
        # SVG yaratish
        svg = generate_svg(project, calculations)
        
        return JsonResponse({
            'success': True,
            'svg': svg
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()  # Konsolga to'liq xatolikni chiqarish
        return JsonResponse({
            'success': False, 
            'error': str(e),
            'traceback': traceback.format_exc()
        }, status=400)

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required

@login_required
def download_svg(request, pk):
    """
    Constructor loyihasi uchun SVG chizmani yuklab olish.
    Agar Project modeli mavjud bo'lsa, undan foydalanadi.
    """
    try:
        # Constructor app dagi Project modelini import qilishga harakat
        from constructor.models import Project
        from constructor.views import calculate_all, generate_svg
        
        project = get_object_or_404(Project, pk=pk)
        
        if not project.calculations:
            calculations = calculate_all(project)
            project.calculations = calculations
            project.save()
        else:
            calculations = project.calculations
        
        svg_content = generate_svg(project, calculations)
        
        response = HttpResponse(svg_content, content_type='image/svg+xml')
        response['Content-Disposition'] = f'attachment; filename="{project.room_code}_technical_sheet.svg"'
        return response
        
    except ImportError:
        # Agar constructor app hali yaratilmagan bo'lsa
        from django.http import HttpResponseNotFound
        return HttpResponseNotFound("Constructor app hali o'rnatilmagan")
    
# constructor/views.py ga qo'shing:

@login_required
def api_project_detail(request, pk):
    """AJAX orqali loyiha ma'lumotlarini olish"""
    project = get_object_or_404(Project, pk=pk)
    
    data = {
        'id': project.id,
        'project_name': project.project_name,
        'room_code': project.room_code,
        'length_m': float(project.length_m),
        'width_m': float(project.width_m),
        'height_m': float(project.height_m),
        'wall_type': project.wall_type,
        'wall_thickness': project.wall_thickness,
        'ceiling_type': project.ceiling_type,
        'ceiling_thickness': project.ceiling_thickness,
        'has_floor': project.has_floor,
        'floor_type': project.floor_type,
        'floor_thickness': project.floor_thickness,
        'panel_width': float(project.panel_width),
        'door_type': project.door_type,
        'door_side': project.door_side,
        'door_position': project.door_position,
        'door_opening': project.door_opening,
        'unit_type': project.unit_type,
        'unit_side': project.unit_side,
        'unit_brand': project.unit_brand,
        'calculations': project.calculations,
    }
    
    return JsonResponse(data)
