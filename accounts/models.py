from django.db import models
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    is_admin = models.BooleanField(default=False)
    display_name = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return self.display_name or self.username
