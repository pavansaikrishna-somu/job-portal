from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from jobs.documents import Job


class JobListAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        query = request.GET.get("q", "").strip().lower()
        location = request.GET.get("location", "").strip().lower()
        jobs = Job.objects.order_by("-created_at")

        payload = []
        for job in jobs:
            if query and query not in f"{job.title} {job.company} {job.description}".lower():
                continue
            if location and location not in job.location.lower():
                continue
            payload.append(
                {
                    "id": str(job.id),
                    "title": job.title,
                    "company": job.company,
                    "location": job.location,
                    "salary": job.salary,
                    "experience": job.experience,
                    "skills_required": job.skills_required,
                    "description": job.description,
                    "recruiter_name": job.recruiter_name,
                    "created_at": job.created_at,
                }
            )
        return Response(payload)
