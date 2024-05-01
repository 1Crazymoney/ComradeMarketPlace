from rest_framework.response import Response
from rest_framework.serializers import ValidationError
from rest_framework import status, viewsets
from rest_framework_simplejwt.tokens import RefreshToken, AccessToken
from rest_framework.decorators import action
from datetime import timedelta
from django.utils import timezone
from django.core.exceptions import ImproperlyConfigured
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.contrib.auth import get_user_model, logout
from .utils import token_generator_and_check_if_exists
from rest_framework_simplejwt.exceptions import TokenError
from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiTypes,
    OpenApiResponse,
)
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from rest_framework_simplejwt.tokens import OutstandingToken, BlacklistedToken
from .models import EmailVerificationToken, PasswordResetToken
from .serializers import (
    AuthUserSerializer,
    UserRegisterSerializer,
    PasswordResetSerializer,
    ErrorSerializer,
    SuccessSerializer,
    AccountProfileSerializer,
    ChangePasswordSerializer,
    EmailVerificationSerializer,
    PasswordResetTokenSerializer,
    RefreshTokenRequestSerializer,
)


User = get_user_model()


class AuthViewSet(viewsets.GenericViewSet):
    serializer_classes = {
        "login": AuthUserSerializer,
        "register": UserRegisterSerializer,
        "verify_email": EmailVerificationSerializer,
        "reset_password": PasswordResetSerializer,
        "send_password_reset_token": PasswordResetTokenSerializer,
        "change_password": ChangePasswordSerializer,
        "get_profile": AccountProfileSerializer,
        "update_profile": AccountProfileSerializer,
        "refresh_token": RefreshTokenRequestSerializer,
    }

    def get_permissions(self):
        permission_classes_dict = {
            "login": [AllowAny],
            "register": [AllowAny],
            "verify_email": [AllowAny],
            "reset_password": [AllowAny],
            "send_password_reset_token": [AllowAny],
            "change_password": [IsAuthenticated],
            "get_profile": [IsAuthenticated],
            "update_profile": [IsAuthenticated],
            "refresh_token": [IsAuthenticated],
            "logout": [IsAuthenticated],
        }
        permission_classes = permission_classes_dict.get(self.action, [IsAuthenticated])
        return [permission() for permission in permission_classes]

    @extend_schema(
        operation_id="login",
        request=AuthUserSerializer,
        responses={
            "200": SuccessSerializer,
            "400": ErrorSerializer,
        },
        description="User login",
    )
    @action(methods=["POST"], detail=False)
    def login(self, request):
        serializer = self.get_serializer_class()
        serializer = serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
            user = serializer.validated_data
            refresh = RefreshToken.for_user(user)
            if not user.is_verified:
                return self.handle_exception_response(
                    "NOT_VERIFIED",
                    "User's email is not verified.",
                    status_response=status.HTTP_400_BAD_REQUEST,
                )

        except ValidationError as e:
            return self.handle_exception_response(
                "VALIDATION_ERROR", "Validation Failed", e, status.HTTP_400_BAD_REQUEST
            )

        except Exception as e:
            return self.handle_exception_response(
                "INTERNAL_SERVER_ERROR",
                "An internal server error occurred",
                e,
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return self.handle_success_response(
            "Login successful.",
            {"refresh": str(refresh), "access": str(refresh.access_token)},
        )

    @extend_schema(
        operation_id="register",
        request=UserRegisterSerializer,
        responses={
            "201": SuccessSerializer,
            "400": ErrorSerializer,
            "500": ErrorSerializer,
        },
        description="User registration",
    )
    @action(
        methods=[
            "POST",
        ],
        detail=False,
    )
    def register(self, request):
        serializer = self.get_serializer_class()
        serializer = serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
            user = serializer.save()
            refresh = RefreshToken.for_user(user)
            email_verification_token = token_generator_and_check_if_exists(
                EmailVerificationToken
            )
            EmailVerificationToken.objects.create(
                token=email_verification_token, user=user
            )
            # TODO:: Send Email
            return self.handle_success_response(
                "User registered successfully.",
                {
                    "refresh": str(refresh),
                    "access": str(refresh.access_token),
                    "email_verification_token": email_verification_token,
                },
            )
        except ValidationError as e:
            return self.handle_exception_response(
                "VALIDATION_ERROR", "Validation Failed", e, status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return self.handle_exception_response(
                "INTERNAL_SERVER_ERROR",
                "An internal server error occurred",
                e,
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        operation_id="verify_user_email",
        parameters=[
            OpenApiParameter(
                name="token",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Email verification token",
            )
        ],
        responses={
            "200": SuccessSerializer,
            "400": ErrorSerializer,
            "500": ErrorSerializer,
        },
        description="Verify user email",
    )
    @action(methods=["GET"], detail=False)
    def verify_email(self, request):
        token = request.query_params.get("token")
        serializer = self.get_serializer_class()
        serializer = serializer(data={"token": token})
        serializer.is_valid(raise_exception=True)

        try:
            token = serializer.validated_data["token"]
            email_verification_token = EmailVerificationToken.objects.get(token=token)
            if (
                email_verification_token.created_at + timedelta(hours=24)
            ) > timezone.now():
                user = email_verification_token.user
                user.is_verified = True
                user.save()
                email_verification_token.delete()
                refresh = RefreshToken.for_user(user)
                response_data = {
                    "message": "Email verified successfully",
                    "data": {
                        "refresh": str(refresh),
                        "access": str(refresh.access_token),
                        "user": user.email,
                    },
                }
                success_serializer = SuccessSerializer(data=response_data)
                success_serializer.is_valid(raise_exception=True)
                return Response(success_serializer.data, status=status.HTTP_200_OK)
            else:
                return self.handle_exception_response(
                    "EXPIRED_TOKEN",
                    "Invalid or expired verification token.",
                    status_response=status.HTTP_400_BAD_REQUEST,
                )
        except EmailVerificationToken.DoesNotExist:
            return self.handle_exception_response(
                "INVALID_TOKEN",
                "Invalid or expired verification token.",
                status_response=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return self.handle_exception_response(
                "INTERNAL_SERVER_ERROR",
                "An internal server error occurred",
                e,
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        operation_id="get_user_profile",
        responses={"200": AccountProfileSerializer, "404": ErrorSerializer},
        description="Get user profile",
    )
    @action(methods=["GET"], detail=False)
    def get_profile(self, request):
        try:
            profile = User.objects.get(email=request.user.email)
            serializer = self.get_serializer_class()
            serializer = serializer(profile)
            return self.handle_success_response(
                "User profile retrieved successfully.", serializer.data
            )
        except User.DoesNotExist:
            return self.handle_exception_response(
                "USER_NOT_FOUND",
                "User not found",
                status_response=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            return self.handle_exception_response(
                "INTERNAL_SERVER_ERROR",
                "An internal server error occurred",
                e,
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        operation_id="update_user_profile",
        request=AccountProfileSerializer,
        responses={
            "200": SuccessSerializer(data=AccountProfileSerializer),
            "404": ErrorSerializer,
        },
        description="Update user profile",
    )
    @action(methods=["PUT"], detail=False)
    def update_profile(self, request):
        try:
            profile = User.objects.get(email=request.user.email)
            serializer = self.get_serializer_class()
            serializer = serializer(profile, data=request.data, partial=True)
            if serializer.is_valid(raise_exception=True):
                serializer.update(profile, request.data)
            else:
                return self.handle_exception_response(
                    "VALIDATION_ERROR",
                    "Validation Failed",
                    e,
                    status.HTTP_400_BAD_REQUEST,
                )
            return self.handle_success_response(
                "User profile updated successfully.", serializer.data
            )
        except User.DoesNotExist:
            return self.handle_exception_response(
                "USER_NOT_FOUND",
                "User not found",
                status_response=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            return self.handle_exception_response(
                "INTERNAL_SERVER_ERROR",
                "An internal server error occurred",
                e,
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        operation_id="change_user_password",
        request=ChangePasswordSerializer,
        responses={"204": None, "400": ErrorSerializer},
        description="Change user password",
    )
    @action(methods=["POST"], detail=False)
    def change_password(self, request):
        serializer = self.get_serializer_class()
        serializer = serializer(data=request.data)
        user = request.user
        try:
            serializer.is_valid(raise_exception=True)
            new_password = serializer.validated_data["new_password"]
            user.set_password(new_password)
            user.save()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except ValidationError as e:
            return self.handle_exception_response(
                "VALIDATION_ERROR", "Validation Failed", e, status.HTTP_400_BAD_REQUEST
            )

        except PasswordResetToken.DoesNotExist:
            return self.handle_exception_response(
                "INVALID_TOKEN",
                "Invalid or expired verification token",
                status_response=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(
        operation_id="logout",
        parameters=[
            OpenApiParameter(
                name="refresh",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Refresh token",
            )
        ],
        responses={"200": SuccessSerializer, "500": ErrorSerializer},
        description="User logout",
    )
    @action(methods=["GET"], detail=False)
    def logout(self, request):
        try:
            refresh_token = request.query_params.get("refresh")
            access_token = request.query_params.get("access")

            if refresh_token:
                token = RefreshToken(refresh_token)
                token.blacklist()

            if access_token:
                token = AccessToken(access_token)
                token.blacklist()

            if not refresh_token and not access_token:
                tokens = RefreshToken.for_user(request.user)
                tokens.access_token.blacklist()
                tokens.blacklist()

        except TokenError as e:
            return self.handle_exception_response(
                "INVALID_REFRESH_TOKEN",
                "Invalid or expired token",
                e,
                status.HTTP_400_BAD_REQUEST,
            )

        except Exception as e:
            return self.handle_exception_response(
                "INTERNAL_SERVER_ERROR",
                "An internal server error occurred",
                e,
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        logout(request)
        return self.handle_success_response("Successfully logged out.")

    @extend_schema(
        operation_id="send_user_password_reset_token",
        request=None,
        parameters=[
            OpenApiParameter(
                name="email",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="User email",
            )
        ],
        responses={"200": SuccessSerializer, "404": ErrorSerializer},
        description="Send password reset token",
    )
    @action(methods=["GET"], detail=False)
    def send_password_reset_token(self, request):
        email = request.query_params.get("email")
        serializer = self.get_serializer_class()
        serializer = serializer(data={"email": email})
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        try:
            user = User.objects.get(email=email)
            password_reset_token = token_generator_and_check_if_exists(
                PasswordResetToken
            )
            PasswordResetToken.objects.create(user=user, token=password_reset_token)
            # TODO:: Send Email
            return self.handle_success_response(
                "Password reset token sent to your email.",
                {
                    "token": password_reset_token,
                },
            )
        except User.DoesNotExist:
            return self.handle_exception_response(
                "USER_NOT_FOUND",
                "User not found",
                status_response=status.HTTP_404_NOT_FOUND,
            )

    @extend_schema(
        operation_id="password_reset",
        request=PasswordResetSerializer,
        responses={"204": None, "400": ErrorSerializer},
        description="Reset user password",
    )
    @action(
        methods=[
            "POST",
        ],
        detail=False,
    )
    def reset_password(self, request):
        serializer = self.get_serializer_class()
        serializer = serializer(data=request.data, context={"request": request})
        try:
            serializer.is_valid(raise_exception=True)
        except ValidationError as e:
            return self.handle_exception_response(
                "VALIDATION_ERROR", "Validation Failed", e, status.HTTP_400_BAD_REQUEST
            )

        user = request.user
        password_reset_token = serializer.validated_data["token"]
        new_password = serializer.validated_data["new_password"]

        try:
            token_obj = PasswordResetToken.objects.get(
                user=user, token=password_reset_token
            )
            if (token_obj.created_at + timedelta(hours=24)) > timezone.now():
                user.set_password(new_password)
                user.save()
                token_obj.delete()
                return Response(status=status.HTTP_204_NO_CONTENT)
            else:
                return self.handle_exception_response(
                    "INVALID_TOKEN",
                    "Password reset token has expired.",
                    status_response=status.HTTP_400_BAD_REQUEST,
                )
        except PasswordResetToken.DoesNotExist:
            return self.handle_exception_response(
                "INVALID_TOKEN",
                "Invalid or expired password reset token",
                e,
                status.HTTP_400_BAD_REQUEST,
            )
        except TypeError as e:
            return self.handle_exception_response(
                "VALIDATION_ERROR", "Validation Failed", e, status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return self.handle_exception_response(
                "INTERNAL_SERVER_ERROR",
                "An internal server error occurred",
                e,
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        operation_id="refresh_token",
        request=RefreshTokenRequestSerializer,
        responses={
            "200": OpenApiResponse(
                response=SuccessSerializer,
                description="Token refreshed successfully",
            ),
            "400": OpenApiResponse(
                response=ErrorSerializer, description="Invalid refresh token"
            ),
        },
        description="Refresh access token",
    )
    @action(methods=["POST"], detail=False)
    def refresh_token(self, request):
        serializer = self.get_serializer_class()
        serializer = serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
            refresh_token = serializer.validated_data["refresh"]
            token = RefreshToken(refresh_token)
            access_token = str(token.access_token)
            return self.handle_success_response(
                "Access token successfully refreshed.",
                {
                    "token": access_token,
                },
            )
        except TokenError as e:
            return self.handle_exception_response(
                "INVALID_REFRESH_TOKEN",
                "Invalid or expired refresh token",
                e,
                status.HTTP_400_BAD_REQUEST,
            )

    def handle_exception_response(
        self, error_code, error_message, details=None, status_response=None
    ):
        error_data = {
            "error_code": error_code,
            "error_message": error_message,
            "details": [str(details) or ""],
        }
        error_serializer = ErrorSerializer(data=error_data)
        error_serializer.is_valid(raise_exception=True)
        return Response(error_serializer.data, status_response)

    def handle_success_response(self, message, data=None):
        response_data = {"message": message, "data": data or {}}
        success_serializer = SuccessSerializer(data=response_data)
        success_serializer.is_valid(raise_exception=True)
        return Response(success_serializer.data, status.HTTP_200_OK)

    def get_serializer_class(self):
        if not isinstance(self.serializer_classes, dict):
            raise ImproperlyConfigured("serializer_classes should be a dict mapping.")
        if self.action in self.serializer_classes.keys():
            return self.serializer_classes[self.action]
        return super().get_serializer_class()