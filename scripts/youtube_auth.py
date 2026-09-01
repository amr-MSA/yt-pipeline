"""
توثيق يوتيوب داخل بيئة GitHub Actions (بدون تدخل بشري).
يعتمد على توكن مُنشأ مسبقًا محليًا (عملية لمرة واحدة) ومحفوظ كـ GitHub Secret،
ثم يجدده تلقائيًا باستخدام refresh_token عند الحاجة.
"""
import json
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


def get_youtube_client(token_json_str: str):
    """
    ياخذ محتوى youtube_token.json (كنص، من متغير بيئة/Secret)
    ويرجع (عميل YouTube API، كائن creds) جاهز. يجدد التوكن تلقائيًا إذا انتهت صلاحيته.

    ملاحظة: access_token له صلاحية قصيرة (~ساعة) ويُجدَّد تلقائيًا هنا في كل تشغيل
    باستخدام refresh_token طويل الأمد، لذلك لا حاجة لتحديث الـ GitHub Secret بشكل
    دوري. الـ Secret يحتاج تحديث فقط إذا تم إلغاء الصلاحية يدويًا من حساب Google.
    """
    token_data = json.loads(token_json_str)
    creds = Credentials.from_authorized_user_info(token_data, SCOPES)

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    if not creds.valid:
        raise RuntimeError(
            "توكن يوتيوب غير صالح ولا يمكن تجديده. "
            "أعد تشغيل عملية التوثيق المحلية وحدّث الـ Secret."
        )

    youtube = build("youtube", "v3", credentials=creds)
    return youtube
