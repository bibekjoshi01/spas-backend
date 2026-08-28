import re
from datetime import timedelta

from django.core import mail
from django.test import override_settings
from django.utils import timezone
from rest_framework import status

from src.user.models import UserForgetPasswordRequest
from tests.test_user_api import BASE, UserAPITestCase


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordResetTests(UserAPITestCase):
    def request_code(self, persona: str):
        return self.client.post(
            f"{BASE}/account/password-reset/request",
            {"persona": persona},
            format="json",
        )

    def latest_code(self) -> str:
        match = re.search(r"\b(\d{6})\b", mail.outbox[-1].body)
        assert match is not None
        return match.group(1)

    def verify_code(self, persona: str, code: str):
        return self.client.post(
            f"{BASE}/account/password-reset/verify",
            {"persona": persona, "code": code},
            format="json",
        )

    @staticmethod
    def reset_token(response) -> str:
        return response.data.get("reset_token") or response.data["resetToken"]

    def test_request_sends_a_six_digit_code_and_stores_only_its_hash(self):
        user = self.make_user("teacher1", "TEACHER")
        response = self.request_code(user.email)

        assert response.status_code == status.HTTP_200_OK
        code = self.latest_code()
        reset_request = UserForgetPasswordRequest.objects.get(user=user)
        assert code not in reset_request.code_hash
        assert reset_request.expires_at > timezone.now()

    def test_request_does_not_reveal_whether_an_account_exists(self):
        existing = self.request_code("missing@college.edu")
        user = self.make_user("teacher1")
        present = self.request_code(user.email)

        assert existing.status_code == present.status_code == status.HTTP_200_OK
        assert existing.data["message"] == present.data["message"]

    def test_a_new_request_invalidates_the_previous_code(self):
        user = self.make_user("teacher1")
        self.request_code(user.email)
        first_code = self.latest_code()
        self.request_code(user.email)

        response = self.verify_code(user.email, first_code)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_expired_code_is_rejected(self):
        user = self.make_user("teacher1")
        self.request_code(user.email)
        code = self.latest_code()
        UserForgetPasswordRequest.objects.filter(user=user).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        assert self.verify_code(user.email, code).status_code == status.HTTP_400_BAD_REQUEST

    def test_five_wrong_codes_lock_the_challenge(self):
        user = self.make_user("teacher1")
        self.request_code(user.email)
        correct_code = self.latest_code()

        for _ in range(5):
            assert self.verify_code(user.email, "000000").status_code == status.HTTP_400_BAD_REQUEST

        assert self.verify_code(user.email, correct_code).status_code == status.HTTP_400_BAD_REQUEST
        assert UserForgetPasswordRequest.objects.get(user=user).is_archived is True

    def test_verified_code_resets_password_once(self):
        user = self.make_user("teacher1")
        self.request_code(user.email)
        verified = self.verify_code(user.email, self.latest_code())
        assert verified.status_code == status.HTTP_200_OK
        token = self.reset_token(verified)

        response = self.client.post(
            f"{BASE}/account/password-reset/confirm",
            {"resetToken": token, "newPassword": "A-Secure-New-Pass!482"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.check_password("A-Secure-New-Pass!482")

        reused = self.client.post(
            f"{BASE}/account/password-reset/confirm",
            {"resetToken": token, "newPassword": "Another-Secure-Pass!482"},
            format="json",
        )
        assert reused.status_code == status.HTTP_400_BAD_REQUEST

    def test_reset_rejects_a_weak_password(self):
        user = self.make_user("teacher1")
        self.request_code(user.email)
        token = self.reset_token(self.verify_code(user.email, self.latest_code()))

        response = self.client.post(
            f"{BASE}/account/password-reset/confirm",
            {"resetToken": token, "newPassword": "123"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_password_reset_revokes_previously_issued_access_tokens(self):
        user = self.make_user("teacher1")
        login_data = self.authenticate("teacher1")
        old_access = login_data["tokens"]["access"]
        self.client.credentials()
        self.request_code(user.email)
        token = self.reset_token(self.verify_code(user.email, self.latest_code()))
        self.client.post(
            f"{BASE}/account/password-reset/confirm",
            {"resetToken": token, "newPassword": "A-Secure-New-Pass!482"},
            format="json",
        )

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {old_access}")
        assert self.client.get(f"{BASE}/account/me").status_code == status.HTTP_401_UNAUTHORIZED
