from django.db import models


class SystemConfig(models.Model):
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField()
    description = models.TextField(blank=True)

    def __str__(self):
        return self.key

    @classmethod
    def get(cls, key, default=None):
        obj = cls.objects.filter(key=key).first()
        if obj is None or obj.value == '':
            return default
        return obj.value

    @classmethod
    def set(cls, key, value, description=''):
        obj, _ = cls.objects.update_or_create(
            key=key,
            defaults={'value': value, 'description': description},
        )
        return obj
