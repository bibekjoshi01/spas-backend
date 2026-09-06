"""Document the tenant JWT subclass using the standard bearer-token scheme."""

from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme


class TenantJWTAuthenticationScheme(SimpleJWTScheme):
    target_class = "src.libs.auth.TenantJWTAuthentication"
    name = "TenantJWTAuth"
