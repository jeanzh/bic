from functools import wraps
from django.shortcuts import redirect
from django.urls import reverse


def admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.is_admin:
            return view_func(request, *args, **kwargs)
        return redirect(reverse('login'))
    return wrapper
