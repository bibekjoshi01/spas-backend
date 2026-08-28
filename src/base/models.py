from uuid import uuid4

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from src.libs.storage import tenant_media_path


class AuditInfoModel(models.Model):
    """Abstract Created Info Model"""

    uuid = models.UUIDField(
        _("uuid"),
        default=uuid4,
        editable=False,
        unique=True,
        help_text=_("Represents unique uuid."),
    )
    created_at = models.DateTimeField(
        _("created date"),
        default=timezone.now,
        editable=False,
    )
    updated_at = models.DateTimeField(_("date updated"), auto_now=True)
    created_by = models.ForeignKey(
        "user.User",
        on_delete=models.PROTECT,
        related_name="+",
    )

    updated_by = models.ForeignKey(
        "user.User",
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        help_text=_("Last user to modify this row; empty until the first update."),
    )
    is_active = models.BooleanField(
        _("active"),
        default=True,
        help_text=_(
            "Designates whether this object should be treated as active. "
            "Unselect this instead of deleting instances.",
        ),
    )
    is_archived = models.BooleanField(
        _("archived"),
        default=False,
        help_text=_(
            "Designates whether this object should be treated as deleted. "
            "Unselect this instead of deleting instances.",
        ),
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.is_archived and self.is_active:
            self.is_active = False
        super().save(*args, **kwargs)

    def get_upload_path(self, filename):
        return tenant_media_path(self, filename)
