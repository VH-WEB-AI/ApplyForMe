# JobDescription lives in resume.py alongside the Resume model since both
# share the same embedding shape and are frequently queried together for
# semantic matching. Re-exported here for a natural import path.
from app.models.resume import JobDescription  # noqa: F401
