from django.urls import path
from . import views

urlpatterns = [
    path('', views.template_list, name='template_list'),
    path('<int:pk>/', views.template_detail, name='template_detail'),
    path('<int:pk>/create/', views.create_ad, name='create_ad'),
    path('creation/<int:pk>/status/', views.creation_status, name='creation_status'),
    path('creation/<int:pk>/status/api/', views.creation_status_api, name='creation_status_api'),
    path('my-creations/', views.my_creations, name='my_creations'),
    path('<int:pk>/like/', views.toggle_like, name='toggle_like'),
    # Admin
    path('admin/upload/', views.admin_upload, name='admin_upload'),
    path('admin/list/', views.admin_templates, name='admin_templates'),
    path('admin/<int:pk>/edit/', views.admin_edit_template, name='admin_edit_template'),
    path('admin/<int:pk>/delete/', views.admin_delete_template, name='admin_delete_template'),
    path('admin/categories/', views.admin_categories, name='admin_categories'),
]
