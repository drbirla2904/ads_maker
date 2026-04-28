from django.db import models
from django.contrib.auth.models import User


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    bio = models.TextField(blank=True)
    is_creator = models.BooleanField(default=False)
    creator_name = models.CharField(max_length=100, blank=True)
    plan = models.CharField(max_length=20, default='free',
                            choices=[('free','Free'),('pro','Pro'),('enterprise','Enterprise')])
    downloads_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} Profile"

    def get_display_name(self):
        return self.creator_name or self.user.get_full_name() or self.user.username
