from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from applications.documents import Application


class MyApplicationsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        applications = Application.objects(applicant_id=request.user.id).order_by("-applied_at")
        payload = [
            {
                "id": str(application.id),
                "job_id": application.job_id,
                "job_title": application.job_title,
                "status": application.status,
                "resume": application.resume,
                "cover_letter": application.cover_letter,
                "applied_at": application.applied_at,
            }
            for application in applications
        ]
        return Response(payload)
