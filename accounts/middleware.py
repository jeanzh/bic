"""临时中间件：当前阶段不需要用户登录，未登录请求一律以管理员身份访问。

移除本中间件（settings.MIDDLEWARE 中删掉该行）即可恢复登录流程。
"""
from django.contrib.auth import get_user_model

User = get_user_model()


class AutoLoginAdminMiddleware:
    """未登录时自动切换到管理员账号（is_admin=True）。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            request.user = self._get_admin_user()
        return self.get_response(request)

    def _get_admin_user(self):
        admin_user = User.objects.filter(is_admin=True, is_active=True).first()
        if admin_user is None:
            admin_user, _ = User.objects.get_or_create(
                username='admin',
                defaults={'is_admin': True, 'display_name': '管理员'},
            )
            if not admin_user.is_admin:
                admin_user.is_admin = True
                admin_user.save(update_fields=['is_admin'])
        return admin_user
