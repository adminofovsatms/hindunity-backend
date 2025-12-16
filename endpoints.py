from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client
from dotenv import load_dotenv
from datetime import datetime, timedelta
import os
import boto3
from botocore.exceptions import ClientError
import time

load_dotenv()

app = Flask(__name__)
CORS(app, origins=["http://localhost:8081", "http://localhost:5173"])

# Supabase client
supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

# S3 client
s3_client = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION", "us-east-1"),
)

S3_BUCKET = os.getenv("AWS_S3_BUCKET")
STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "tweets-media")


class AuthManager:
    """Simple auth manager to cache Supabase bot auth token."""

    def __init__(self):
        self.token = None
        self.user_id = None
        self.expires_at = None

    def get_token(self):
        # Return cached token if still valid
        if self.token and datetime.now() < self.expires_at:
            print("Using cached token")
            return self.token

        # Login and refresh token
        auth = supabase.auth.sign_in_with_password(
            {
                "email": os.getenv("BOT_EMAIL"),
                "password": os.getenv("BOT_PASSWORD"),
            }
        )

        if not auth or not auth.session:
            raise Exception("Bot login failed")

        self.token = auth.session.access_token
        self.user_id = auth.user.id
        self.expires_at = datetime.now() + timedelta(hours=1)
        print("Logged in and refreshed token")
        return self.token

    def get_user_id(self):
        # Ensure we have a valid token (will login if needed)
        self.get_token()
        return self.user_id


# Global auth manager
auth_manager = AuthManager()


def delete_media_from_storage(media_urls):
    """Delete media files from S3."""
    if not media_urls:
        return

    try:
        print(f"\n🗑️ Deleting {len(media_urls)} media files from S3...")

        for media_url in media_urls:
            try:
                # Extract S3 key from public URL
                if f"{S3_BUCKET}.s3.amazonaws.com/" in media_url:
                    s3_key = media_url.split(f"{S3_BUCKET}.s3.amazonaws.com/")[1]

                    # Delete from S3
                    s3_client.delete_object(
                        Bucket=S3_BUCKET,
                        Key=s3_key,
                    )
                    print(f"   ✅ Deleted: {s3_key}")
                else:
                    print(f"   ⚠️ Invalid URL format: {media_url}")

            except Exception as e:  # noqa: BLE001
                print(f"   ❌ Failed to delete {media_url}: {str(e)}")

    except Exception as e:  # noqa: BLE001
        print(f"❌ Error deleting media: {str(e)}")


@app.route("/api/get-upload-url", methods=["POST"])
def get_upload_url():
    """Generate presigned URL for S3 upload."""
    try:
        data = request.json or {}
        user_id = data.get("user_id")
        file_type = data.get("file_type")  # "image" or "video"
        file_name = data.get("file_name")
        content_type = data.get("content_type")

        # Validate
        if not all([user_id, file_type, file_name, content_type]):
            return jsonify({"error": "Missing required fields"}), 400

        # Generate S3 key
        timestamp = int(time.time() * 1000)
        file_ext = file_name.split(".")[-1]
        folder = "post-images" if file_type == "image" else "post-videos"
        s3_key = f"{folder}/{user_id}/{timestamp}.{file_ext}"

        # Generate presigned URL (expires in 5 minutes)
        presigned_url = s3_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": S3_BUCKET,
                "Key": s3_key,
                "ContentType": content_type,
            },
            ExpiresIn=300,  # 5 minutes
        )

        # Public URL for database
        public_url = f"https://{S3_BUCKET}.s3.amazonaws.com/{s3_key}"

        return (
            jsonify(
                {
                    "upload_url": presigned_url,
                    "public_url": public_url,
                    "s3_key": s3_key,
                }
            ),
            200,
        )

    except Exception as e:  # noqa: BLE001
        print(f"❌ Error generating presigned URL: {str(e)}")
        import traceback

        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/get-avatar-upload-url", methods=["POST"])
def get_avatar_upload_url():
    """Generate presigned URL for uploading/updating user avatar."""
    try:
        data = request.json or {}
        user_id = data.get("user_id")
        file_name = data.get("file_name")
        content_type = data.get("content_type")

        # Validate
        if not all([user_id, file_name, content_type]):
            return jsonify({"error": "Missing required fields"}), 400

        # Extract file extension
        file_ext = file_name.split(".")[-1]

        # S3 key: always the same per user to overwrite old avatar
        s3_key = f"avatars/{user_id}/avatar.{file_ext}"

        # Generate presigned URL (expires in 5 minutes)
        presigned_url = s3_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": S3_BUCKET,
                "Key": s3_key,
                "ContentType": content_type,
            },
            ExpiresIn=300,  # 5 minutes
        )

        # Public URL for database
        public_url = f"https://{S3_BUCKET}.s3.amazonaws.com/{s3_key}"

        return (
            jsonify(
                {
                    "upload_url": presigned_url,
                    "public_url": public_url,
                    "s3_key": s3_key,
                }
            ),
            200,
        )

    except Exception as e:  # noqa: BLE001
        print(f"❌ Error generating presigned URL: {str(e)}")
        import traceback

        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/botposts", methods=["POST"])
def create_post_by_bot():
    """
    Create a single post with tweet data and media URLs.

    Expected JSON:
    {
        "content": "tweet text",
        "post_type": "text",
        "media_url": ["url1", "url2", ...] or null,
        "twitter_unique_id": "message_id",
        "twitter_username": "username",
        "source": "twitter",
        "location": null
    }
    """
    try:
        # Get fresh/cached token
        token = auth_manager.get_token()
        user_id = auth_manager.get_user_id()

        # Attach token to client
        supabase.postgrest.auth(token)

        # Get request data
        data = request.json

        if not data:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "No data provided",
                    }
                ),
                400,
            )

        # Validate required fields
        if not data.get("content"):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "content is required",
                    }
                ),
                400,
            )

        if not data.get("twitter_unique_id"):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "twitter_unique_id is required",
                    }
                ),
                400,
            )

        # Prepare post data
        post_data = {
            "user_id": user_id,
            "content": data.get("content"),
            "post_type": data.get("post_type", "text"),
            "media_url": data.get("media_url"),  # Array of URLs or None
            "twitter_unique_id": data.get("twitter_unique_id"),
            "twitter_username": data.get("twitter_username"),
            "source": data.get("source", "twitter"),
            "location": data.get("location"),
        }

        media_urls = data.get("media_url", [])

        print(f"\n💾 Creating post for tweet: {data.get('twitter_unique_id')}")
        if media_urls:
            print(f"   📎 With {len(media_urls)} media files")

        # Insert post to database
        response = supabase.table("posts").insert(post_data).execute()

        print("✅ Post created successfully")

        return (
            jsonify(
                {
                    "success": True,
                    "data": response.data,
                    "message": "Post created successfully",
                }
            ),
            201,
        )

    except Exception as e:  # noqa: BLE001
        print(f"\n❌ Error creating post: {str(e)}")

        # If post insertion failed, delete uploaded media from S3
        media_urls = request.json.get("media_url", []) if request.json else []
        if media_urls:
            print(
                f"⚠️ Post insertion failed - cleaning up {len(media_urls)} media files..."
            )
            delete_media_from_storage(media_urls)

        import traceback

        traceback.print_exc()

        return (
            jsonify(
                {
                    "success": False,
                    "error": str(e),
                }
            ),
            500,
        )


@app.route("/pendingbotposts", methods=["POST"])
def create_post_by_bot_for_approval():
    """
    Create a single post with tweet data and media URLs for approval queue.

    Same payload as /botposts but writes to twitter_posts table.
    """
    try:
        # Get fresh/cached token
        token = auth_manager.get_token()
        user_id = auth_manager.get_user_id()

        # Attach token to client
        supabase.postgrest.auth(token)

        # Get request data
        data = request.json

        if not data:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "No data provided",
                    }
                ),
                400,
            )

        # Validate required fields
        if not data.get("content"):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "content is required",
                    }
                ),
                400,
            )

        if not data.get("twitter_unique_id"):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "twitter_unique_id is required",
                    }
                ),
                400,
            )

        # Prepare post data
        post_data = {
            "user_id": user_id,
            "content": data.get("content"),
            "post_type": data.get("post_type", "text"),
            "media_url": data.get("media_url"),  # Array of URLs or None
            "twitter_unique_id": data.get("twitter_unique_id"),
            "twitter_username": data.get("twitter_username"),
            "source": data.get("source", "twitter"),
            "location": data.get("location"),
        }

        media_urls = data.get("media_url", [])

        print(f"\n💾 Creating post for tweet: {data.get('twitter_unique_id')}")
        if media_urls:
            print(f"   📎 With {len(media_urls)} media files")

        # Insert post to database
        response = supabase.table("twitter_posts").insert(post_data).execute()

        print("✅ Post created successfully")

        return (
            jsonify(
                {
                    "success": True,
                    "data": response.data,
                    "message": "Post created successfully",
                }
            ),
            201,
        )

    except Exception as e:  # noqa: BLE001
        print(f"\n❌ Error creating post: {str(e)}")

        # If post insertion failed, delete uploaded media from S3
        media_urls = request.json.get("media_url", []) if request.json else []
        if media_urls:
            print(
                f"⚠️ Post insertion failed - cleaning up {len(media_urls)} media files..."
            )
            delete_media_from_storage(media_urls)

        import traceback

        traceback.print_exc()

        return (
            jsonify(
                {
                    "success": False,
                    "error": str(e),
                }
            ),
            500,
        )


@app.route("/delete-media", methods=["POST"])
def delete_media():
    """Delete media files from S3 using provided URLs."""
    try:
        data = request.json or {}
        media_urls = data.get("media_urls", [])

        if not media_urls:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "No media URLs provided",
                    }
                ),
                400,
            )

        print(f"\n🗑️ Deleting {len(media_urls)} media files from S3")
        delete_media_from_storage(media_urls)

        print("✅ Media deleted successfully")

        return (
            jsonify(
                {
                    "success": True,
                    "message": f"Deleted {len(media_urls)} media files",
                }
            ),
            200,
        )

    except Exception as e:  # noqa: BLE001
        print(f"\n❌ Error deleting media: {str(e)}")
        import traceback

        traceback.print_exc()

        return (
            jsonify(
                {
                    "success": False,
                    "error": str(e),
                }
            ),
            500,
        )


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint."""
    return (
        jsonify(
            {
                "success": True,
                "message": "Server is running",
            }
        ),
        200,
    )