from users.services import get_user_profile


def current_user_profile(request):
    if request.user.is_authenticated:
        return {"current_user_profile": get_user_profile(request.user.id)}
    return {"current_user_profile": None}
