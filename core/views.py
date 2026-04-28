from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from templates_app.models import AdTemplate, UserCreation

def home(request):
    featured = AdTemplate.objects.filter(is_active=True, is_featured=True)[:6]
    popular = AdTemplate.objects.filter(is_active=True).order_by('-likes_count')[:8]
    newest = AdTemplate.objects.filter(is_active=True).order_by('-created_at')[:8]
    total_templates = AdTemplate.objects.filter(is_active=True).count()
    return render(request, 'core/home.html', {
        'featured': featured,
        'popular': popular,
        'newest': newest,
        'total_templates': total_templates,
    })

@login_required
def dashboard(request):
    creations = UserCreation.objects.filter(user=request.user).order_by('-created_at')[:10]
    return render(request, 'core/dashboard.html', {'creations': creations})
