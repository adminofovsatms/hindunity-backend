from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta
import os
import time

app = Flask(__name__)

# CORS configuration
ALLOWED_ORIGINS = {
    "http://localhost:8081",
    "http://localhost:5173",
    "https://server.onehindus.com",
    "https://onehindus.com",
    "https://www.onehindus.com",
}

CORS(
    app,
    resources={r"/api/*": {"origins": list(ALLOWED_ORIGINS)}},
    supports_credentials=True,
)


@app.after_request
def add_cors_headers(response):
    """Ensure CORS headers are always present for allowed origins."""
    origin = request.headers.get("Origin")
    if origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


# ============================================
# LAZY INITIALIZATION - Required for Vercel
# ============================================


_supabase = None
_supabase_admin = None
_s3_client = None

def get_supabase():
    """Lazy initialization of Supabase client"""
    global _supabase
    if _supabase is None:
        from supabase import create_client
        _supabase = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_KEY")
        )
    return _supabase

def get_supabase_admin():
    """Lazy initialization of Supabase admin client"""
    global _supabase_admin
    if _supabase_admin is None:
        from supabase import create_client
        _supabase_admin = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_KEY")
        )
    return _supabase_admin

def get_s3_client():
    """Lazy initialization of S3 client"""
    global _s3_client
    if _s3_client is None:
        import boto3
        _s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION', 'us-east-1')
        )
    return _s3_client 

def get_s3_bucket():
    return os.getenv('AWS_S3_BUCKET')

DEFAULT_BOT_USER_ID = "bdb9c10d-1127-476e-8b71-18acecc74824"


# Auth manager class
class AuthManager:
    def __init__(self):
        self.token = None
        self.user_id = None
        self.expires_at = None
    
    def get_token(self):
        # Return cached token if still valid
        if self.token and self.expires_at and datetime.now() < self.expires_at:
            print("Using cached token")
            return self.token
        
        # Login and refresh token
        supabase = get_supabase()
        auth = supabase.auth.sign_in_with_password({
            "email": os.getenv("BOT_EMAIL"),
            "password": os.getenv("BOT_PASSWORD")
        })
        
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
    """Delete media files from S3"""
    if not media_urls:
        return
    
    try:
        s3_client = get_s3_client()
        s3_bucket = get_s3_bucket()
        print(f"\n🗑️ Deleting {len(media_urls)} media files from S3...")
        
        for media_url in media_urls:
            try:
                if f'{s3_bucket}.s3.amazonaws.com/' in media_url:
                    s3_key = media_url.split(f'{s3_bucket}.s3.amazonaws.com/')[1]
                    
                    s3_client.delete_object( 
                        Bucket=s3_bucket,
                        Key=s3_key
                    )
                    print(f"   ✅ Deleted: {s3_key}")
                else:
                    print(f"   ⚠️ Invalid URL format: {media_url}")
                    
            except Exception as e:
                print(f"   ❌ Failed to delete {media_url}: {str(e)}")
                
    except Exception as e:
        print(f"❌ Error deleting media: {str(e)}")


# ============================================
# HEALTH CHECK
# ============================================

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "success": True,
        "message": "Server is running"
    }), 200


# ============================================
# UPLOAD ENDPOINTS
# ============================================

@app.route('/api/get-upload-url', methods=['OPTIONS', 'POST'])
def get_upload_url():
    """Generate presigned URL for S3 upload"""
    if request.method == "OPTIONS":
        return "", 200
    try:
        data = request.json
        user_id = data.get('user_id')
        file_type = data.get('file_type')
        file_name = data.get('file_name')
        content_type = data.get('content_type') 
        
        if not all([user_id, file_type, file_name, content_type]):
            return jsonify({'error': 'Missing required fields'}), 400
        
        s3_client = get_s3_client()
        s3_bucket = get_s3_bucket()
        
        timestamp = int(time.time() * 1000)
        file_ext = file_name.split('.')[-1]
        folder = 'post-images' if file_type == 'image' else 'post-videos'
        s3_key = f"{folder}/{user_id}/{timestamp}.{file_ext}"
        
        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': s3_bucket,
                'Key': s3_key,
                'ContentType': content_type
            },
            ExpiresIn=300
        )
        
        public_url = f"https://{s3_bucket}.s3.amazonaws.com/{s3_key}"
        
        return jsonify({
            'upload_url': presigned_url,
            'public_url': public_url,
            's3_key': s3_key
        }), 200
        
    except Exception as e:
        print(f"❌ Error generating presigned URL: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/get-avatar-upload-url', methods=['POST'])
def get_avatar_upload_url():
    """Generate presigned URL for uploading/updating user avatar"""
    try:
        data = request.json
        user_id = data.get('user_id')
        file_name = data.get('file_name')
        content_type = data.get('content_type')

        if not all([user_id, file_name, content_type]):
            return jsonify({'error': 'Missing required fields'}), 400

        s3_client = get_s3_client()
        s3_bucket = get_s3_bucket()

        file_ext = file_name.split('.')[-1]
        s3_key = f"avatars/{user_id}/avatar.{file_ext}"

        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': s3_bucket,
                'Key': s3_key,
                'ContentType': content_type
            },
            ExpiresIn=300
        )

        public_url = f"https://{s3_bucket}.s3.amazonaws.com/{s3_key}"

        return jsonify({
            'upload_url': presigned_url,
            'public_url': public_url,
            's3_key': s3_key
        }), 200

    except Exception as e:
        print(f"❌ Error generating presigned URL: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================
# BOT POSTS ENDPOINTS
# ============================================

@app.route('/botposts', methods=['OPTIONS', 'POST'])
def create_post_by_bot():
    """Create a single post with tweet data and media URLs"""
    if request.method == "OPTIONS":
        return "", 200
    try:
        token = auth_manager.get_token()
        user_id = auth_manager.get_user_id()
        
        supabase = get_supabase()
        supabase.postgrest.auth(token)
        
        data = request.json
        
        if not data:
            return jsonify({
                "success": False,
                "error": "No data provided"
            }), 400
        
        if not data.get("content"):
            return jsonify({
                "success": False,
                "error": "content is required"
            }), 400
        
        if not data.get("twitter_unique_id"):
            return jsonify({
                "success": False,
                "error": "twitter_unique_id is required"
            }), 400
        
        post_data = {
            "user_id": user_id,
            "content": data.get("content"),
            "post_type": data.get("post_type", "text"),
            "media_url": data.get("media_url"),
            "twitter_unique_id": data.get("twitter_unique_id"),
            "twitter_username": data.get("twitter_username"),
            "source": data.get("source", "twitter"),
            "location": data.get("location"),
            "link_preview": data.get("link_preview")
        }
        
        media_urls = data.get("media_url", [])
        
        print(f"\n💾 Creating post for tweet: {data.get('twitter_unique_id')}")
        if media_urls:
            print(f"   📎 With {len(media_urls)} media files")
        
        response = supabase.table('posts').insert(post_data).execute()
        
        print(f"✅ Post created successfully")
        
        return jsonify({
            "success": True,
            "data": response.data,
            "message": "Post created successfully"
        }), 201
        
    except Exception as e:
        print(f"\n❌ Error creating post: {str(e)}")
        
        media_urls = request.json.get("media_url", []) if request.json else []
        if media_urls:
            print(f"⚠️ Post insertion failed - cleaning up {len(media_urls)} media files...")
            delete_media_from_storage(media_urls)
        
        import traceback
        traceback.print_exc()
        
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/pendingbotposts', methods=['OPTIONS', 'POST'])
def create_post_by_bot_for_approval():
    """Create a single post with tweet data and media URLs for approval"""
    if request.method == "OPTIONS":
        return "", 200
    try:
        data = request.json
        
        if not data:
            return jsonify({
                "success": False,
                "error": "No data provided"
            }), 400
        
        if not data.get("content"):
            return jsonify({
                "success": False,
                "error": "content is required"
            }), 400
        
        if not data.get("twitter_unique_id"):
            return jsonify({
                "success": False,
                "error": "twitter_unique_id is required"
            }), 400
        
        twitter_username = data.get("twitter_username")
        supabase_admin = get_supabase_admin()
        
        user_id = None
        if twitter_username:
            try:
                print(f"\n🔍 Looking up user_id for Twitter username: {twitter_username}")
                mapping_response = supabase_admin.table('twitter_id_map').select('user_id').eq('username', twitter_username).execute()
                
                if mapping_response.data and len(mapping_response.data) > 0:
                    user_id = mapping_response.data[0]['user_id']
                    print(f"   ✓ Found user_id: {user_id}")
                else:
                    user_id = DEFAULT_BOT_USER_ID
                    print(f"   ⚠ Username not found in mapping, using default user_id: {user_id}")
            except Exception as e:
                print(f"   ✗ Error fetching from twitter_id_map: {e}")
                user_id = DEFAULT_BOT_USER_ID
                print(f"   ⚠ Using default user_id: {user_id}")
        else:
            user_id = DEFAULT_BOT_USER_ID
            print(f"   ⚠ No twitter_username provided, using default user_id: {user_id}")
        
        post_data = {
            "user_id": user_id,
            "content": data.get("content"),
            "post_type": data.get("post_type", "text"),
            "media_url": data.get("media_url"),
            "twitter_unique_id": data.get("twitter_unique_id"),
            "twitter_username": twitter_username,
            "source": data.get("source", "twitter"),
            "location": data.get("location"),
            "link_preview": data.get("link_preview")
        }
        
        media_urls = data.get("media_url", [])
        
        print(f"\n💾 Creating post for tweet: {data.get('twitter_unique_id')}")
        print(f"   👤 User ID: {user_id}")
        if media_urls:
            print(f"   📎 With {len(media_urls)} media files")
        
        response = supabase_admin.table('twitter_posts').insert(post_data).execute()
        
        print(f"✅ Post created successfully")
        
        return jsonify({
            "success": True,
            "data": response.data,
            "message": "Post created successfully"
        }), 201
        
    except Exception as e:
        print(f"\n❌ Error creating post: {str(e)}")
        
        media_urls = request.json.get("media_url", []) if request.json else []
        if media_urls:
            print(f"⚠️ Post insertion failed - cleaning up {len(media_urls)} media files...")
            delete_media_from_storage(media_urls)
        
        import traceback
        traceback.print_exc()
        
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============================================
# MEDIA & USER MANAGEMENT
# ============================================

@app.route('/delete-media', methods=['POST'])
def delete_media():
    """Delete media files from S3 using provided URLs"""
    try:
        data = request.json
        media_urls = data.get('media_urls', [])
        
        if not media_urls:
            return jsonify({
                "success": False,
                "error": "No media URLs provided"
            }), 400
        
        print(f"\n🗑️ Deleting {len(media_urls)} media files from S3")
        delete_media_from_storage(media_urls)
        
        print(f"✅ Media deleted successfully")
        
        return jsonify({
            "success": True,
            "message": f"Deleted {len(media_urls)} media files"
        }), 200
        
    except Exception as e:
        print(f"\n❌ Error deleting media: {str(e)}")
        import traceback
        traceback.print_exc()
        
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/delete-user', methods=['OPTIONS', 'POST'])
def delete_user():
    """Delete a user account using Supabase Admin API"""
    if request.method == "OPTIONS":
        return "", 200
    
    try:
        data = request.json
        
        if not data:
            return jsonify({
                "success": False,
                "error": "No data provided"
            }), 400
        
        user_id = data.get('user_id')
        
        if not user_id:
            return jsonify({
                "success": False,
                "error": "user_id is required"
            }), 400
        
        print(f"\n🗑️ Deleting user: {user_id}")
        
        supabase_admin = get_supabase_admin()
        response = supabase_admin.auth.admin.delete_user(user_id)
        
        print(f"✅ User deleted successfully: {user_id}")
        
        return jsonify({
            "success": True,
            "message": "User deleted successfully"
        }), 200
        
    except Exception as e:
        print(f"\n❌ Error deleting user: {str(e)}")
        import traceback
        traceback.print_exc()
        
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/admin/accept-twitter-post', methods=['POST'])
def accept_twitter_post():
    """Accept a twitter post and transfer it to posts table"""
    try:
        data = request.json
        
        if not data or not data.get('twitter_unique_id'):
            return jsonify({
                "success": False,
                "error": "twitter_unique_id is required"
            }), 400
        
        twitter_unique_id = data.get('twitter_unique_id')
        supabase_admin = get_supabase_admin()
        
        print(f"\n✅ Accepting Twitter post: {twitter_unique_id}")
        
        update_response = supabase_admin.table('twitter_posts').update({
            'status': 'accepted'
        }).eq('twitter_unique_id', twitter_unique_id).execute()
        
        if not update_response.data:
            return jsonify({
                "success": False,
                "error": "Twitter post not found"
            }), 404
        
        post = update_response.data[0]
        
        post_data = {
            "user_id": post['user_id'],
            "content": post['content'],
            "post_type": post['post_type'],
            "media_url": post['media_url'],
            "twitter_unique_id": post['twitter_unique_id'],
            "twitter_username": post['twitter_username'],
            "source": post['source'],
            "location": post['location'],
            "link_preview": post['link_preview']
        }
        
        insert_response = supabase_admin.table('posts').insert(post_data).execute()
        
        print(f"✅ Post transferred successfully")
        
        return jsonify({
            "success": True,
            "data": insert_response.data,
            "message": "Post accepted and published successfully"
        }), 200
        
    except Exception as e:
        print(f"\n❌ Error accepting post: {str(e)}")
        import traceback
        traceback.print_exc()
        
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

