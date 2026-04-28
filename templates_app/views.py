import json
import os
import threading
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from .models import AdTemplate, Category, TemplateLike, UserCreation


def _run_processing(creation_id):
    """Thread target — imports renderer inside thread to avoid DB issues."""
    from core.renderer import process_creation
    process_creation(creation_id)


def template_list(request):
    templates = AdTemplate.objects.filter(is_active=True)
    categories = Category.objects.all()

    q = request.GET.get('q', '')
    if q:
        templates = templates.filter(
            Q(title__icontains=q) | Q(tags__icontains=q) | Q(description__icontains=q)
        )
    category = request.GET.get('category', '')
    if category:
        templates = templates.filter(category__slug=category)
    file_type = request.GET.get('type', '')
    if file_type:
        templates = templates.filter(file_type=file_type)
    is_free = request.GET.get('free', '')
    if is_free:
        templates = templates.filter(is_free=True)

    sort = request.GET.get('sort', 'newest')
    if sort == 'popular':
        templates = templates.order_by('-likes_count', '-uses_count')
    elif sort == 'most_used':
        templates = templates.order_by('-uses_count')
    else:
        templates = templates.order_by('-created_at')

    paginator = Paginator(templates, 12)
    page = paginator.get_page(request.GET.get('page', 1))

    liked_ids = set()
    if request.user.is_authenticated:
        liked_ids = set(TemplateLike.objects.filter(
            user=request.user).values_list('template_id', flat=True))

    return render(request, 'templates_app/list.html', {
        'page_obj': page, 'categories': categories,
        'liked_ids': liked_ids, 'current_q': q,
        'current_category': category, 'current_type': file_type,
        'current_sort': sort, 'current_free': is_free,
    })


def template_detail(request, pk):
    template = get_object_or_404(AdTemplate, pk=pk, is_active=True)
    is_liked = False
    if request.user.is_authenticated:
        is_liked = TemplateLike.objects.filter(user=request.user, template=template).exists()
    related = AdTemplate.objects.filter(is_active=True, category=template.category).exclude(pk=pk)[:4]
    return render(request, 'templates_app/detail.html', {
        'template': template, 'is_liked': is_liked, 'related': related,
    })


@login_required
def create_ad(request, pk):
    template = get_object_or_404(AdTemplate, pk=pk, is_active=True)
    if request.method == 'POST':
        user_file = request.FILES.get('user_file')
        if not user_file:
            messages.error(request, 'Please upload a file.')
            return redirect('template_detail', pk=pk)

        fname = user_file.name.lower()
        file_type = 'video' if fname.endswith(('.mp4', '.mov', '.avi', '.webm')) else 'image'

        # Crop parameters from the browser cropper
        crop_x = request.POST.get('crop_x', '0')
        crop_y = request.POST.get('crop_y', '0')
        crop_w = request.POST.get('crop_w', '0')
        crop_h = request.POST.get('crop_h', '0')

        creation = UserCreation.objects.create(
            user=request.user,
            template=template,
            user_file=user_file,
            user_file_type=file_type,
            status='pending',
            # Store crop params in error_message field temporarily (reuse field)
        )

        # Store crop data on creation object via a JSON side-channel
        # We'll use a simple approach: save as metadata
        try:
            cx, cy = int(float(crop_x)), int(float(crop_y))
            cw, ch = int(float(crop_w)), int(float(crop_h))
            if cw > 10 and ch > 10:
                import json as _j
                creation.error_message = _j.dumps({
                    'crop': {'x': cx, 'y': cy, 'w': cw, 'h': ch}
                })
                creation.save(update_fields=['error_message'])
        except (ValueError, TypeError):
            pass

        t = threading.Thread(target=_run_processing, args=(creation.pk,))
        t.daemon = True
        t.start()

        return redirect('creation_status', pk=creation.pk)

    return render(request, 'templates_app/create.html', {'template': template})


@login_required
def creation_status(request, pk):
    creation = get_object_or_404(UserCreation, pk=pk, user=request.user)
    return render(request, 'templates_app/status.html', {'creation': creation})


@login_required
def creation_status_api(request, pk):
    creation = get_object_or_404(UserCreation, pk=pk, user=request.user)
    return JsonResponse({
        'status': creation.status,
        'output_url': creation.output_file.url if creation.output_file else None,
        'thumbnail_url': creation.output_thumbnail.url if creation.output_thumbnail else None,
        'error': creation.error_message,
    })


@login_required
def my_creations(request):
    creations = UserCreation.objects.filter(user=request.user)
    return render(request, 'templates_app/my_creations.html', {'creations': creations})


@login_required
@require_POST
def toggle_like(request, pk):
    template = get_object_or_404(AdTemplate, pk=pk)
    like, created = TemplateLike.objects.get_or_create(user=request.user, template=template)
    if not created:
        like.delete()
        template.likes_count = max(0, template.likes_count - 1)
        liked = False
    else:
        template.likes_count += 1
        liked = True
    template.save(update_fields=['likes_count'])
    return JsonResponse({'liked': liked, 'count': template.likes_count})


# ─── ADMIN ────────────────────────────────────────────────────────────────────

@staff_member_required
def admin_upload(request):
    categories = Category.objects.all()
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        if not title or not request.FILES.get('template_file'):
            messages.error(request, 'Title and template file are required.')
            return render(request, 'templates_app/admin_upload.html', {'categories': categories})

        template_file = request.FILES['template_file']
        fname = template_file.name.lower()
        file_type = 'video' if fname.endswith(('.mp4', '.mov', '.avi', '.webm')) else 'image'

        category = None
        cat_id = request.POST.get('category')
        if cat_id:
            try:
                category = Category.objects.get(pk=cat_id)
            except Category.DoesNotExist:
                pass

        zone_data_str = request.POST.get('zone_data', '{}') or '{}'

        t = AdTemplate.objects.create(
            title=title,
            description=request.POST.get('description', ''),
            creator=request.user,
            category=category,
            tags=request.POST.get('tags', ''),
            file=template_file,
            file_type=file_type,
            orientation=request.POST.get('orientation', 'portrait'),
            canvas_width=int(request.POST.get('canvas_width', 1080) or 1080),
            canvas_height=int(request.POST.get('canvas_height', 1920) or 1920),
            zone_data=zone_data_str,
            is_free=request.POST.get('is_free') == 'on',
            is_featured=request.POST.get('is_featured') == 'on',
        )
        if request.FILES.get('thumbnail_file'):
            t.thumbnail = request.FILES['thumbnail_file']
            t.save()

        # ── Auto-detect chroma key + preprocess for video templates ──
        chroma_info = None
        if file_type == 'video':
            try:
                import json as _json
                from core.renderer import detect_chroma_color, preprocess_template_video
                from django.conf import settings as _s

                chroma_info = detect_chroma_color(t.file.path)
                if chroma_info:
                    try:
                        zone = _json.loads(zone_data_str)
                    except:
                        zone = {}
                    zone.update({
                        'chroma_hex':      chroma_info['hex'],
                        'chroma_rgb':      list(chroma_info['rgb']),
                        'chroma_sim':      chroma_info['similarity'],
                        'chroma_blend':    chroma_info['blend'],
                        'chroma_type':     chroma_info['type'],
                        'chroma_coverage': chroma_info['coverage_pct'],
                        'x':      chroma_info['bbox']['x'],
                        'y':      chroma_info['bbox']['y'],
                        'width':  chroma_info['bbox']['w'],
                        'height': chroma_info['bbox']['h'],
                        'bbox':   chroma_info['bbox'],
                    })
                    t.zone_data = _json.dumps(zone)
                    t.save(update_fields=['zone_data'])

                    # Preprocess: remove placeholder text from template video
                    # Run in background thread so upload response is fast
                    import threading
                    def _preprocess(template_id, zone_dict, media_root):
                        try:
                            from templates_app.models import AdTemplate as _AT
                            _t = _AT.objects.get(pk=template_id)
                            updated_zone = preprocess_template_video(
                                _t.file.path, zone_dict, media_root)
                            _t.zone_data = _json.dumps(updated_zone)
                            _t.save(update_fields=['zone_data'])
                            print(f"Template {template_id} preprocessed ✅")
                        except Exception as e:
                            print(f"Preprocess error: {e}")
                    th = threading.Thread(
                        target=_preprocess,
                        args=(t.pk, zone, _s.MEDIA_ROOT)
                    )
                    th.daemon = True
                    th.start()

            except Exception as e:
                print(f"Chroma detection error: {e}")
                import traceback; traceback.print_exc()

        if chroma_info:
            messages.success(
                request,
                f'✅ Template "{title}" uploaded! '
                f'Chroma zone auto-detected (#{chroma_info["hex"].upper()}). '
                f'Preprocessing in background — will be ready in ~1 min.'
            )
        else:
            messages.success(request, f'Template "{title}" uploaded!')
        return redirect('admin_templates')

    return render(request, 'templates_app/admin_upload.html', {'categories': categories})


@staff_member_required
def admin_templates(request):
    templates = AdTemplate.objects.all().order_by('-created_at')
    return render(request, 'templates_app/admin_list.html', {'templates': templates})


@staff_member_required
def admin_edit_template(request, pk):
    template = get_object_or_404(AdTemplate, pk=pk)
    categories = Category.objects.all()
    if request.method == 'POST':
        template.title = request.POST.get('title', template.title)
        template.description = request.POST.get('description', template.description)
        template.tags = request.POST.get('tags', template.tags)
        template.is_free = request.POST.get('is_free') == 'on'
        template.is_featured = request.POST.get('is_featured') == 'on'
        template.is_active = request.POST.get('is_active') == 'on'
        template.orientation = request.POST.get('orientation', template.orientation)
        zd = request.POST.get('zone_data', '')
        if zd:
            template.zone_data = zd
        cat_id = request.POST.get('category')
        if cat_id:
            try:
                template.category = Category.objects.get(pk=cat_id)
            except Category.DoesNotExist:
                pass
        if request.FILES.get('thumbnail_file'):
            template.thumbnail = request.FILES['thumbnail_file']
        template.save()
        messages.success(request, 'Template updated!')
        return redirect('admin_templates')

    return render(request, 'templates_app/admin_edit.html', {
        'template': template, 'categories': categories,
    })


@staff_member_required
@require_POST
def admin_delete_template(request, pk):
    get_object_or_404(AdTemplate, pk=pk).delete()
    messages.success(request, 'Template deleted.')
    return redirect('admin_templates')


@staff_member_required
def admin_categories(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        slug = request.POST.get('slug', '').strip()
        icon = request.POST.get('icon', '📁')
        if name and slug:
            Category.objects.get_or_create(slug=slug, defaults={'name': name, 'icon': icon})
            messages.success(request, f'Category "{name}" created.')
        return redirect('admin_categories')
    return render(request, 'templates_app/admin_categories.html', {'categories': Category.objects.all()})
