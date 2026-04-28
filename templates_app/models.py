from django.db import models
from django.contrib.auth.models import User
import json


class Category(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    icon = models.CharField(max_length=50, default='📁')

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'Categories'


class AdTemplate(models.Model):
    TYPE_IMAGE = 'image'
    TYPE_VIDEO = 'video'
    TYPE_CHOICES = [(TYPE_IMAGE, 'Image'), (TYPE_VIDEO, 'Video')]

    ORIENTATION_PORTRAIT = 'portrait'
    ORIENTATION_LANDSCAPE = 'landscape'
    ORIENTATION_SQUARE = 'square'
    ORIENTATION_CHOICES = [
        (ORIENTATION_PORTRAIT, 'Portrait'),
        (ORIENTATION_LANDSCAPE, 'Landscape'),
        (ORIENTATION_SQUARE, 'Square'),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    creator = models.ForeignKey(User, on_delete=models.CASCADE, related_name='templates')
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    tags = models.CharField(max_length=500, blank=True, help_text="Comma separated tags")

    # The actual template file (image or video)
    file = models.FileField(upload_to='templates/files/')
    thumbnail = models.ImageField(upload_to='templates/thumbnails/', blank=True, null=True)
    file_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_IMAGE)
    orientation = models.CharField(max_length=20, choices=ORIENTATION_CHOICES, default=ORIENTATION_PORTRAIT)

    # Canvas dimensions of the template
    canvas_width = models.IntegerField(default=1080)
    canvas_height = models.IntegerField(default=1920)

    # The replaceable zone - stored as JSON
    # Format: {"x": 100, "y": 200, "width": 400, "height": 300, "rotation": 15,
    #          "points": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]}
    zone_data = models.TextField(default='{}', help_text="JSON data for replaceable zone")

    is_free = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)

    likes_count = models.IntegerField(default=0)
    uses_count = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

    def get_zone(self):
        try:
            return json.loads(self.zone_data)
        except:
            return {}

    def get_tags_list(self):
        return [t.strip() for t in self.tags.split(',') if t.strip()]

    class Meta:
        ordering = ['-created_at']


class TemplateLike(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    template = models.ForeignKey(AdTemplate, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'template')


class UserCreation(models.Model):
    """Stores user-generated ads"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='creations')
    template = models.ForeignKey(AdTemplate, on_delete=models.CASCADE)

    # User's uploaded content
    user_file = models.FileField(upload_to='user_uploads/')
    user_file_type = models.CharField(max_length=10, default='image')

    # Final rendered output
    output_file = models.FileField(upload_to='outputs/', blank=True, null=True)
    output_thumbnail = models.ImageField(upload_to='output_thumbnails/', blank=True, null=True)

    status = models.CharField(max_length=20, default='pending',
                              choices=[('pending','Pending'),('processing','Processing'),
                                       ('done','Done'),('failed','Failed')])
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.template.title}"

    class Meta:
        ordering = ['-created_at']
