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
    
    # Guruhlar tekshiruvi
    is_glavniy_admin = request.user.is_superuser or is_in_group(request.user, 'Glavniy Admin')
    is_production_boss = is_in_group(request.user, "Ishlab Chiqarish Boshlig'i")
    is_manager_or_confirmer = is_in_group(request.user, 'Menejer/Tasdiqlovchi')
    is_worker = is_in_group(request.user, 'Usta')
    is_observer = is_in_group(request.user, 'Kuzatuvchi')

    # Filtr parametri
    filter_type = request.GET.get('filter', 'all')  # all, completed, in_progress, overdue
    
    # 1. HAMMA BUYURTMALAR (arxivdagilar ham)
    all_orders_qs = Order.objects.all()
    
    # 2. Arxivdagilar sonini hisoblash uchun (agar kerak bo'lsa)
    archived_count = Order.objects.filter(
        status__in=['BAJARILDI', 'USTA_TUGATDI', 'TAYYOR']
    ).count()
    
    # 3. ASOSIY QUERYSET - HAMMA BUYURTMALAR (arxivdagilar bilan birga)
    base_qs = Order.objects.all()  # Hech narsani exclude qilmaymiz
    
    # ASOSIY BUYURTMALAR (parent_order=None)
    main_orders = base_qs.filter(parent_order__isnull=True).order_by('-created_at')
    
    # CHILD BUYURTMALAR - PANEL va UGUL
    all_child_orders = base_qs.filter(parent_order__isnull=False).order_by('-created_at')
    
    # Panel child orderlar
    panel_child_orders = all_child_orders.filter(
        Q(product_name__icontains='panel') | 
        Q(product_name__icontains='панель') |
        Q(product_name__icontains='панел')
    )
    
    # Ugul child orderlar
    ugul_child_orders = all_child_orders.filter(
        Q(product_name__icontains='ugul') | 
        Q(product_name__icontains='угол') |
        Q(product_name__icontains='уголь')
    )
    
    # Boshqa child orderlar
    other_child_orders = all_child_orders.exclude(
        Q(product_name__icontains='panel') | 
        Q(product_name__icontains='панель') |
        Q(product_name__icontains='панел') |
        Q(product_name__icontains='ugul') | 
        Q(product_name__icontains='угол') |
        Q(product_name__icontains='уголь')
    )
    
    # Hammasini vaqt bo'yicha ko'rsatadi
    orders = base_qs.all().order_by('-created_at')
    customers_count = Order.objects.values('customer_unique_id').distinct().count()
    
    # Filtrlash
    now = timezone.now()
    if filter_type == 'completed':
        # Tayyor buyurtmalar
        main_orders = main_orders.filter(status__in=['TAYYOR', 'BAJARILDI', 'USTA_TUGATDI'])
        panel_child_orders = panel_child_orders.filter(status__in=['TAYYOR', 'BAJARILDI', 'USTA_TUGATDI'])
        ugul_child_orders = ugul_child_orders.filter(status__in=['TAYYOR', 'BAJARILDI', 'USTA_TUGATDI'])
        other_child_orders = other_child_orders.filter(status__in=['TAYYOR', 'BAJARILDI', 'USTA_TUGATDI'])
        orders = orders.filter(status__in=['TAYYOR', 'BAJARILDI', 'USTA_TUGATDI'])
    elif filter_type == 'in_progress':
        # Jarayondagi buyurtmalar - FAQAT MUDDATI O'TMAGANLAR
        main_orders = main_orders.exclude(status__in=['TAYYOR', 'BAJARILDI', 'USTA_TUGATDI', 'RAD_ETILDI']).filter(
            Q(deadline__isnull=True) | Q(deadline__gte=now)
        )
        panel_child_orders = panel_child_orders.exclude(status__in=['TAYYOR', 'BAJARILDI', 'USTA_TUGATDI', 'RAD_ETILDI']).filter(
            Q(deadline__isnull=True) | Q(deadline__gte=now)
        )
        ugul_child_orders = ugul_child_orders.exclude(status__in=['TAYYOR', 'BAJARILDI', 'USTA_TUGATDI', 'RAD_ETILDI']).filter(
            Q(deadline__isnull=True) | Q(deadline__gte=now)
        )
        other_child_orders = other_child_orders.exclude(status__in=['TAYYOR', 'BAJARILDI', 'USTA_TUGATDI', 'RAD_ETILDI']).filter(
            Q(deadline__isnull=True) | Q(deadline__gte=now)
        )
        orders = orders.exclude(status__in=['TAYYOR', 'BAJARILDI', 'USTA_TUGATDI', 'RAD_ETILDI']).filter(
            Q(deadline__isnull=True) | Q(deadline__gte=now)
        )
    elif filter_type == 'overdue':
        # Muddati o'tgan buyurtmalar - FAQAT JARAYONDAGI VA MUDDATI O'TGANLAR
        main_orders = main_orders.filter(
            deadline__lt=now
        ).exclude(
            status__in=['BAJARILDI', 'USTA_TUGATDI', 'RAD_ETILDI', 'TAYYOR']
        )
        panel_child_orders = panel_child_orders.filter(
            deadline__lt=now
        ).exclude(
            status__in=['BAJARILDI', 'USTA_TUGATDI', 'RAD_ETILDI', 'TAYYOR']
        )
        ugul_child_orders = ugul_child_orders.filter(
            deadline__lt=now
        ).exclude(
            status__in=['BAJARILDI', 'USTA_TUGATDI', 'RAD_ETILDI', 'TAYYOR']
        )
        other_child_orders = other_child_orders.filter(
            deadline__lt=now
        ).exclude(
            status__in=['BAJARILDI', 'USTA_TUGATDI', 'RAD_ETILDI', 'TAYYOR']
        )
        orders = orders.filter(
            deadline__lt=now
        ).exclude(
            status__in=['BAJARILDI', 'USTA_TUGATDI', 'RAD_ETILDI', 'TAYYOR']
        )
    
    # Filterlash mantigi
    should_filter = True 

    if is_glavniy_admin or is_production_boss or is_manager_or_confirmer or is_observer:
        should_filter = False 
    
    if is_worker:
        if not (is_glavniy_admin or is_production_boss or is_manager_or_confirmer or is_observer):
            try:
                # Ustaga tayinlangan barcha buyurtmalar ro'yxatini olamiz
                orders = orders.filter(
                    assigned_workers__user=request.user, 
                ).exclude(
                    status='RAD_ETILDI'
                ).distinct().order_by('-created_at')
                
                main_orders = main_orders.filter(
                    assigned_workers__user=request.user, 
                ).exclude(
                    status='RAD_ETILDI'
                ).distinct().order_by('-created_at')
                
                panel_child_orders = panel_child_orders.filter(
                    assigned_workers__user=request.user, 
                ).exclude(
                    status='RAD_ETILDI'
                ).distinct().order_by('-created_at')
                
                ugul_child_orders = ugul_child_orders.filter(
                    assigned_workers__user=request.user, 
                ).exclude(
                    status='RAD_ETILDI'
                ).distinct().order_by('-created_at')
                
                other_child_orders = other_child_orders.filter(
                    assigned_workers__user=request.user, 
                ).exclude(
                    status='RAD_ETILDI'
                ).distinct().order_by('-created_at')
                
                if not orders.exists():
                    messages.info(request, "Sizga tayinlangan buyurtmalar topilmadi.")

            except Exception:
                orders = orders.none() 
                main_orders = main_orders.none()
                panel_child_orders = panel_child_orders.none()
                ugul_child_orders = ugul_child_orders.none()
                other_child_orders = other_child_orders.none()
                messages.warning(request, "Buyurtmalarni yuklashda xato: Usta profili noto'g'ri bog'langan bo'lishi mumkin.")
            
            should_filter = True

    if should_filter and not is_worker:
        pass

    # Muddat buzilishini tekshirish
    if is_glavniy_admin or is_production_boss:
        overdue_orders = main_orders.filter(
            deadline__lt=timezone.now(),
            status__in=['TASDIQLANDI', 'USTA_QABUL_QILDI', 'USTA_BOSHLA', 'ISHDA', 'KIRITILDI']
        )
        for order in overdue_orders:
            check_and_create_overdue_alerts(order)

    user_notifications = Notification.objects.filter(user=request.user, is_read=False)[:5]
    
    # STATISTIKA
    total_orders = main_orders.count()  # Barcha asosiy buyurtmalar (arxivdagilar bilan)
    completed_orders = main_orders.filter(status__in=['TAYYOR', 'BAJARILDI', 'USTA_TUGATDI']).count()
    
    # Jarayondagi buyurtmalar soni
    in_progress_orders = main_orders.exclude(
        status__in=['TAYYOR', 'BAJARILDI', 'USTA_TUGATDI', 'RAD_ETILDI']
    ).filter(
        Q(deadline__isnull=True) | Q(deadline__gte=now)
    ).count()
    
    # Muddati o'tgan buyurtmalar soni
    overdue_orders_count = main_orders.filter(
        deadline__lt=now
    ).exclude(
        status__in=['BAJARILDI', 'USTA_TUGATDI', 'RAD_ETILDI', 'TAYYOR']
    ).count()
    
    # Child orderlar statistikasi
    all_child_orders_count = all_child_orders.count()
    panel_child_count = panel_child_orders.count()
    ugul_child_count = ugul_child_orders.count()
    other_child_count = other_child_orders.count()
    
    panel_completed = panel_child_orders.filter(status__in=['TAYYOR', 'BAJARILDI', 'USTA_TUGATDI']).count()
    ugul_completed = ugul_child_orders.filter(status__in=['TAYYOR', 'BAJARILDI', 'USTA_TUGATDI']).count()
    
    unpaid_orders = Order.objects.none()
    total_unpaid_amount = 0
    unpaid_orders_count = 0

    if is_glavniy_admin or is_manager_or_confirmer:
        unpaid_orders = Order.objects.filter(
            parent_order__isnull=True,
            total_price__gt=F('prepayment')
        ).exclude(status='BEKOR_QILINDI')
        unpaid_orders_count = unpaid_orders.count()
        total_unpaid_amount = sum(order.remaining_amount for order in unpaid_orders)
    
    can_view_orders = any([
        is_glavniy_admin, 
        is_production_boss, 
        is_manager_or_confirmer, 
        is_worker, 
        is_observer
    ])

    context = {
        'archived_count': archived_count,
        'orders': orders,
        'unpaid_orders_count': unpaid_orders_count,
        'total_unpaid_amount': total_unpaid_amount,
        'main_orders': main_orders,
        'panel_child_orders': panel_child_orders,
        'ugul_child_orders': ugul_child_orders,
        'other_child_orders': other_child_orders,
        'is_glavniy_admin': is_glavniy_admin,
        'is_manager': is_manager_or_confirmer, 
        'is_production_boss': is_production_boss,
        'is_worker': is_worker,
        'is_observer': is_observer,
        'notifications': user_notifications, 
        'now': timezone.now(),
        'filter_type': filter_type,
        'total_orders': total_orders,
        'completed_orders': completed_orders,
        'in_progress_orders': in_progress_orders,
        'overdue_orders_count': overdue_orders_count,
        'all_child_orders_count': all_child_orders_count,
        'panel_child_count': panel_child_count,
        'ugul_child_count': ugul_child_count,
        'other_child_count': other_child_count,
        'panel_completed': panel_completed,
        'ugul_completed': ugul_completed,
        'is_storekeeper': request.user.username.lower() == 'omborchi' or 'store' in request.user.username.lower(),
        'customers_count': customers_count,
        'can_view_orders': can_view_orders,
    }
    return render(request, 'orders/order_list.html', context)



from django.db.models import Q

@login_required
def order_archive(request):
    search_query = request.GET.get('q', '')
    
    # Arxiv statuslari
    archive_statuses = ['BAJARILDI', 'USTA_TUGATDI', 'TAYYOR']
    
    # Asosiy buyurtmalar
    main_orders = Order.objects.filter(
        parent_order__isnull=True, 
        status__in=archive_statuses
    ).order_by('-created_at')
    
    # Ichki buyurtmalar
    child_orders = Order.objects.filter(
        parent_order__isnull=False, 
        status__in=archive_statuses
    ).order_by('-created_at')

    # Agar qidiruv bo'lsa
    if search_query:
        main_orders = main_orders.filter(
            Q(id__icontains=search_query) |
            Q(customer_name__icontains=search_query) |
            Q(product_name__icontains=search_query)
        )
        child_orders = child_orders.filter(
            Q(id__icontains=search_query) |
            Q(product_name__icontains=search_query)
        )

    context = {
        'main_orders': main_orders,
        'child_orders': child_orders,
        'search_query': search_query,
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


@login_required
@staff_member_required
def warehouse_dashboard(request):
    """Ombordagi barcha materiallar qoldig'i"""
    materials = Material.objects.all().order_by('quantity')
    return render(request, 'orders/warehouse_dashboard.html', {'materials': materials})
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

# TELEGRAM CONFIG
TELEGRAM_BOT_TOKEN = "8559719741:AAGa5BnxXt2rxjC-gKFnzboBiJQgPUY2GzU"
TELEGRAM_GROUP_ID = "-1002338157363"

def is_in_group(user, group_name):
    return user.groups.filter(name=group_name).exists()

@login_required
def order_detail(request, pk):
    order = get_object_or_404(Order, pk=pk)

    # Ruxsatlarni tekshirish
    is_glavniy_admin = request.user.is_superuser or is_in_group(request.user, 'Glavniy Admin')
    is_manager = is_in_group(request.user, 'Menejer/Tasdiqlovchi')
    is_production_boss = is_in_group(request.user, "Ishlab Chiqarish Boshlig'i")
    is_worker = is_in_group(request.user, 'Usta') 
    is_observer = is_in_group(request.user, 'Kuzatuvchi')

    # Kuzatuvchi uchun readonly
    if is_observer:
        context = {'order': order, 'readonly': True}
        return render(request, 'orders/order_detail.html', context)

    # Usta tayinlanganligini tekshirish
    is_assigned_worker = False
    if is_worker:
        try:
            worker_profile = request.user.worker_profile
            is_assigned_worker = order.assigned_workers.filter(pk=worker_profile.pk).exists()
        except Exception:
            is_assigned_worker = False

    if is_worker and not is_assigned_worker and not is_production_boss:
        messages.error(request, "Siz faqat o'zingizga tayinlangan buyurtma tafsilotlarini ko'rishingiz mumkin.")
        return redirect('order_list')

    # Admin/Manager/Boss uchun OrderForm
    order_form = None
    if is_glavniy_admin or is_manager or is_production_boss:
        order_form = OrderForm(request.POST or None, instance=order)
        if request.method == 'POST' and 'upload_type' not in request.POST:
            if order_form.is_valid():
                order_form.save()
                messages.success(request, "Buyurtma ma'lumotlari muvaffaqiyatli yangilandi.")
                return redirect('order_detail', pk=order.pk)
            else:
                messages.error(request, "Buyurtma ma'lumotlarini saqlashda xatolik yuz berdi.")

    # POST: start / finish rasm yuborish (Telegramga)
    if request.method == 'POST' and is_assigned_worker:
        upload_type = request.POST.get('upload_type')  # start_image / finish_image
        image = request.FILES.get(upload_type)

        if image and upload_type in ['start_image', 'finish_image']:
            caption = (
                f"🧾 BUYURTMA: #{order.id}\n"
                f"👷 Usta: @{request.user.username}\n"
                f"📌 Holat: {'Boshlash' if upload_type=='start_image' else 'Tugatish'}\n"
                f"🕒 Vaqt: {timezone.now().strftime('%Y-%m-%d %H:%M')}"
            )

            # Telegramga yuborish
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
                requests.post(url, data={"chat_id": TELEGRAM_GROUP_ID, "caption": caption}, files={"photo": image})
            except Exception as e:
                messages.error(request, f"Telegramga yuborishda xatolik: {str(e)}")
                return redirect('order_detail', pk=order.pk)

            # Ma'lumotlarni saqlash
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
            messages.success(request, f"{'Boshlash' if upload_type=='start_image' else 'Tugatish'} rasmi Telegramga yuborildi.")
        else:
            messages.error(request, "Rasm yuklanmadi yoki noto‘g‘ri action.")
        return redirect('order_detail', pk=order.pk)

    # GET so‘rov
    context = {
        'order': order,
        'order_form': order_form,
        'is_worker': is_worker,
        'is_assigned_worker': is_assigned_worker,
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
@login_required
@user_passes_test(
    lambda u: u.is_superuser or is_in_group(u, 'Glavniy Admin') or is_in_group(u, 'Manager'),
    login_url='/login/'
)
def order_create(request):
    """Buyurtma yaratish - mijoz uchun bir martalik unikal raqam bilan"""
    
    if request.method == 'POST':
        form = OrderForm(request.POST, request.FILES)
        
        if form.is_valid():
            try:
                order = form.save(commit=False)
                order.created_by = request.user
                
                # CUSTOMER LOGIC
                customer_unique_id = form.cleaned_data.get('customer_unique_id', '').strip()
                customer_name = form.cleaned_data.get('customer_name', '').strip()
                customer_phone = form.cleaned_data.get('customer_phone', '').strip() or None

                if not customer_unique_id:
                    messages.error(request, "❌ Iltimos, mijoz uchun unikal raqam kiriting.")
                    return render(request, 'orders/order_create.html', {'form': form})

                customer, created = Customer.objects.get_or_create(
                    unique_id=customer_unique_id,
                    defaults={'name': customer_name, 'phone': customer_phone}
                )
                order.customer = customer

                # Ish turi va holat
                worker_type = form.cleaned_data.get('worker_type', 'LIST')
                order.status = 'KIRITILDI'

                # ✅ ESHIK maxsus saqlash
                if worker_type == 'ESHIK':
                    eshik_turi = form.cleaned_data.get('eshik_turi', '')
                    zamokli_eshik = form.cleaned_data.get('zamokli_eshik', False)
                    if '(' not in str(eshik_turi):
                        zamok_status = "Zamokli" if zamokli_eshik else "Zamoksiz"
                        order.eshik_turi = f"{eshik_turi} ({zamok_status})" if eshik_turi else ""

                # ✅ PANEL USTALARINI AVTOMATIK TAQSIMLASH (TOQ/JUFT)
                if worker_type == 'PANEL':
                    try:
                        u1 = User.objects.get(username='panel_usta')
                        u2 = User.objects.get(username='panel_usta2')
                        
                        # Oxirgi panel buyurtmasini ID bo'yicha olamiz
                        last_panel_order = Order.objects.filter(worker_type='PANEL').order_by('-id').first()
                        
                        if last_panel_order:
                            # Agar oxirgi ID toq bo'lsa -> panel_usta2, juft bo'lsa -> panel_usta
                            if last_panel_order.id % 2 != 0:
                                order.assigned_to = u2
                            else:
                                order.assigned_to = u1
                        else:
                            order.assigned_to = u1 # Birinchi marta u1 dan boshlaymiz
                    except User.DoesNotExist:
                        pass # Agar userlar topilmasa, assigned_to bo'sh qoladi

                order.needs_manager_approval = form.cleaned_data.get('needs_manager_approval', True)
                order.panel_thickness = form.cleaned_data.get('panel_thickness')
                
                # Saqlash
                order.save()
                form.save_m2m()
                
                send_notifications_for_new_order(order)

                if worker_type == 'LIST':
                    messages.info(request, "⚠️ List usta ishni tugatgandan so'ng, Panel va Ugol ustalari uchun avtomatik buyurtmalar yaratiladi.")
                
                messages.success(request, f"✅ Buyurtma №{order.order_number} kiritildi! Usta: {order.assigned_to if order.assigned_to else 'Belgilanmagan'}")
                return redirect('order_list')
                
            except Exception as e:
                messages.error(request, f"❌ Xatolik: {str(e)}")
                return render(request, 'orders/order_create.html', {'form': form})
    else:
        form = OrderForm()
    
    return render(request, 'orders/order_create.html', {'form': form, 'title': 'Yangi Buyurtma Kiritish'})

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


@login_required
def order_confirm(request, pk):
    """2-Bosqich: Buyurtmani tasdiqlash."""
    # Kuzatuvchi tekshiruvi
    if is_observer(request.user):
        messages.error(request, "Kuzatuvchi rejimida bu amalni bajarish mumkin emas.")
        return redirect('order_list')
        
    order = get_object_or_404(Order, pk=pk)
    
    if not is_in_group(request.user, 'Menejer/Tasdiqlovchi'): 
        messages.error(request, "Sizda bu buyurtmani tasdiqlash uchun ruxsat yo'q.")
        return redirect('order_list')

    if order.status == 'KIRITILDI':
        order.status = 'TASDIQLANDI'
        order.save()
        
        LogEntry.objects.log_action(
            user_id=request.user.id,
            content_type_id=ContentType.objects.get_for_model(order).pk,
            object_id=order.pk,
            object_repr=str(order),
            action_flag=CHANGE,
            change_message=f"Status o'zgartirildi: KIRITILDI -> TASDIQLANDI"
        )
        
        messages.success(request, f"Buyurtma №{order.order_number} Tasdiqlandi.")
        
        if order.created_by:
            Notification.objects.create(
                user=order.created_by,
                order=order,
                message=f"Siz kiritgan buyurtma №{order.order_number} Muvaffaqiyatli Tasdiqlandi."
            )
        
        try:
            boss_group = Group.objects.get(name="Ishlab Chiqarish Boshlig'i")
            for boss in boss_group.user_set.all():
                Notification.objects.create(
                    user=boss,
                    order=order,
                    message=f"Yangi buyurtma №{order.order_number} Tasdiqlandi. Ishlab chiqarishni boshlashingiz mumkin."
                )
        except Group.DoesNotExist:
            messages.warning(request, "Ishlab Chiqarish Boshlig'i guruhi topilmadi.")

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

@login_required
def order_start_production(request, pk):
    """3-Bosqich: Ishlab chiqarishga berish."""
    # Kuzatuvchi tekshiruvi
    if is_observer(request.user):
        messages.error(request, "Kuzatuvchi rejimida bu amalni bajarish mumkin emas.")
        return redirect('order_list')
        
    order = get_object_or_404(Order, pk=pk)
    
    if not is_in_group(request.user, "Ishlab Chiqarish Boshlig'i"):
        messages.error(request, "Ishlab chiqarishni boshlash uchun ruxsat yo'q.")
        return redirect('order_list')

    if order.status == 'TASDIQLANDI':
        order.status = 'ISHDA'
        order.save()
        
        LogEntry.objects.log_action(
            user_id=request.user.id,
            content_type_id=ContentType.objects.get_for_model(order).pk,
            object_id=order.pk,
            object_repr=str(order),
            action_flag=CHANGE,
            change_message=f"Status o'zgartirildi: TASDIQLANDI -> ISHDA"
        )
        
        messages.info(request, f"Buyurtma №{order.order_number} ishlab chiqarishga berildi.")
        
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
@login_required
def order_finish(request, pk):
    """4-Bosqich: Buyurtmani yakunlash."""
    if is_observer(request.user):
        messages.error(request, "Kuzatuvchi rejimida bu amalni bajarish mumkin emas.")
        return redirect('order_list')
        
    order = get_object_or_404(Order, pk=pk)
    
    if not is_in_group(request.user, "Ishlab Chiqarish Boshlig'i"):
        messages.error(request, "Buyurtmani yakunlash uchun ruxsat yo'q.")
        return redirect('order_list')

    if order.status in ['ISHDA', 'USTA_TUGATDI']:
        # DIQQAT: Zanjir ishlashi uchun statusni USTA_TUGATDI qilib saqlash kerak
        # Agar hozir TAYYOR qilsangiz, modeldagi 'if status == USTA_TUGATDI' sharti ishlamay qoladi.
        
        order.status = 'USTA_TUGATDI' 
        order.save() # Shu yerda modeldagi save() ishlaydi va yangi order ochadi
        
        # 🔴 Eski create_panel_ugol_orders() metodini o'chirib tashladik!
        # Chunki hamma ishni yuqoridagi order.save() avtomat bajaradi.
        
        if order.worker_type in ['LIST', 'ESHIK', 'LIST_ESHIK']:
            messages.info(request, "Usta ishini tugatdi. Navbatdagi bosqich (Panel) avtomatik yaratildi.")

        LogEntry.objects.log_action(
            user_id=request.user.id,
            content_type_id=ContentType.objects.get_for_model(order).pk,
            object_id=order.pk,
            object_repr=str(order),
            action_flag=CHANGE,
            change_message=f"Status o'zgartirildi: {order.get_status_display()}"
        )
        
        messages.success(request, f"Buyurtma №{order.order_number} yakunlandi.")
        
        # Notificationlar qismi (o'zgarishsiz qoladi)
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
from django.contrib.admin.models import LogEntry, ADDITION
from django.contrib.contenttypes.models import ContentType

@login_required
@user_passes_test(lambda u: u.is_superuser or is_in_group(u, 'Glavniy Admin'), login_url='/login/')
def order_create(request):
    """1-Bosqich: Buyurtmani yuklash/kiritish."""
    if is_observer(request.user):
        messages.error(request, "Kuzatuvchi rejimida bu amalni bajarish mumkin emas.")
        return redirect('order_list')
        
    if request.method == 'POST':
        form = OrderForm(request.POST, request.FILES)
        if form.is_valid():
            order = form.save(commit=False)
            order.created_by = request.user
            order.status = 'KIRITILDI' 
            
            order.save()
            form.save_m2m() # 🔴 assigned_workers shu yerda saqlanadi

            # ✅ LOG YOZISH (To'g'rilangan variant)
            LogEntry.objects.create(
                user_id=request.user.id,
                content_type_id=ContentType.objects.get_for_model(order).pk,
                object_id=order.pk,
                object_repr=str(order),
                action_flag=ADDITION,
                change_message=f"Yangi buyurtma kiritildi: №{order.order_number} (Turi: {order.get_worker_type_display()})"
            )

            messages.success(request, f"Buyurtma №{order.order_number} kiritildi. Ish turi: {order.get_worker_type_display()}")

            # 🔴 Notification 1: Menejerlarga
            try:
                manager_group = Group.objects.get(name='Menejer/Tasdiqlovchi') 
                for manager in manager_group.user_set.all():
                    Notification.objects.create(
                        user=manager,
                        order=order,
                        message=f"Yangi buyurtma: №{order.order_number}. Tasdiqlash talab qilinadi."
                    )
            except Group.DoesNotExist:
                pass

            # 🔴 Notification 2: Biriktirilgan ustalarga
            workers = order.assigned_workers.all()
            if workers.exists():
                for worker in workers:
                    Notification.objects.create(
                        user=worker.user,
                        order=order,
                        message=f"№{order.order_number} buyurtmasi sizga tayinlandi. Rolingiz: {worker.get_role_display()}"
                    )
            
            return redirect('order_list')
    else:
        form = OrderForm()
    
    return render(request, 'orders/order_create.html', {'form': form})
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

@login_required
def debt_report(request):
    # Shartlar: 
    # 1. Jami pul to'langan puldan (zalogdan) katta bo'lsin (Qarz bor degani)
    # 2. Status bekor qilinmagan bo'lishi shart
    debts = Order.objects.filter(
        total_price__gt=F('prepayment')
    ).exclude(status='BEKOR_QILINDI').order_by('-created_at')

    # Umumiy qarz summasini hisoblash
    # remaining_amount modeldagi property yoki metod bo'lishi kerak
    total_debt = sum(order.remaining_amount for order in debts)

    context = {
        'debts': debts,
        'total_debt': total_debt,
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

    # ======================== 2. MIJOZLAR REYTINGI (Annotate) ========================
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
    sum_ratings = list(ratings_query.order_by('-total_paid')[:15])
    order_count_ratings = list(ratings_query.order_by('-order_count')[:10])
    loyal_customers = list(ratings_query.filter(loyalty_score='A').order_by('-total_paid')[:10])

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

    completed_orders = Order.objects.filter(parent_order__isnull=True, status__in=['completed', 'delivered']).count()
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

  # ... (oldingi hisob-kitoblar) ...

    # ======================== 5. PIR PANELLAR TAHLILI ========================
    pir_all = Order.objects.filter(panel_type__icontains='PIR')
    
    pir_stats = pir_all.values('panel_type').annotate(
        count=Count('id'),
        total_m2=Coalesce(Sum('panel_kvadrat'), Value(0, output_field=DecimalField())),
        total_revenue=Coalesce(Sum('total_price'), Value(0, output_field=DecimalField()))
    ).order_by('-total_m2')

    pir_details = {
        'total_pir': pir_all.count(),
        'total_area': pir_all.aggregate(s=Sum('panel_kvadrat'))['s'] or 0,
        'tom_panels': pir_all.filter(Q(panel_subtype__icontains='TOM') | Q(product_name__icontains='TOM')).count(),
        'secret_panels': pir_all.filter(Q(panel_subtype__icontains='SECRET') | Q(product_name__icontains='SECRET')).count(),
        'sovut_panels': pir_all.filter(Q(panel_subtype__icontains='SOVUT') | Q(product_name__icontains='SOVUT')).count(),
    }

    # ======================== 6. ESHIKLAR TAHLILI ========================
    eshik_stat = Order.objects.filter(parent_order__isnull=True).exclude(
        Q(eshik_turi__isnull=True) | Q(eshik_turi='')
    ).values('eshik_turi').annotate(
        eshik_soni=Count('id'),
        total_revenue=Sum('total_price')
    ).order_by('-eshik_soni')

    # ======================== 7. MASHHUR MAHSULOTLAR ========================
    product_rankings = Order.objects.filter(parent_order__isnull=True).values('product_name').annotate(
        order_count=Count('id'),
        total_m2=Coalesce(Sum('panel_kvadrat'), Value(0, output_field=DecimalField())),
        total_revenue=Coalesce(Sum('total_price'), Value(0, output_field=DecimalField()))
    ).order_by('-order_count')[:15]

    if product_rankings:
        max_orders = max(p['order_count'] for p in product_rankings)
        for p in product_rankings:
            p['popularity_score'] = (p['order_count'] * 100) / max_orders if max_orders > 0 else 0

    # ======================== 8. CONTEXT (TO'G'IRLANGAN) ========================
    # DIQQAT: Bu yerda context = {} deb yangidan ochmang, hamma o'zgaruvchini shu yerga jamlang
    context = {
        'm2_ratings': m2_ratings,
        'sum_ratings': sum_ratings,
        'order_count_ratings': order_count_ratings,
        'loyal_customers': loyal_customers,
        'overall_stats': overall_stats,
        'product_rankings': list(product_rankings),
        'thickness_stat': list(thickness_stat),
        'pir_stats': list(pir_stats),
        'pir_details': pir_details,  # <--- BU ENDI O'CHIB KETMAYDI
        'eshik_stat': list(eshik_stat),
        'json_data': {
            'm2_ratings': json.dumps(list(m2_ratings), default=str),
            'sum_ratings': json.dumps(list(sum_ratings), default=str),
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
