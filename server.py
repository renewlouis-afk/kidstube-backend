from fastapi import FastAPI, APIRouter, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
import uuid
from datetime import datetime, timedelta
import base64
import io
import json
import asyncio
import httpx
import random

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
# Support MongoDB Atlas SSL connections
import ssl
import certifi
if 'mongodb+srv' in mongo_url or 'mongodb.net' in mongo_url:
    client = AsyncIOMotorClient(
        mongo_url,
        tls=True,
        tlsCAFile=certifi.where(),
        tlsAllowInvalidCertificates=True,
        serverSelectionTimeoutMS=10000,
        connectTimeoutMS=10000
    )
else:
    client = AsyncIOMotorClient(mongo_url)
db = client[os.environ.get('DB_NAME', 'kidstube_creator')]

# API Keys
YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY', '')
ELEVENLABS_API_KEY = os.environ.get('ELEVENLABS_API_KEY', '')
EMERGENT_LLM_KEY = os.environ.get('EMERGENT_LLM_KEY', '')

# YouTube OAuth
YOUTUBE_CLIENT_ID = os.environ.get('YOUTUBE_CLIENT_ID', '')
YOUTUBE_CLIENT_SECRET = os.environ.get('YOUTUBE_CLIENT_SECRET', '')
YOUTUBE_REDIRECT_URI = os.environ.get('YOUTUBE_REDIRECT_URI', '')

# Create directories for generated content
GENERATED_DIR = ROOT_DIR / 'generated'
GENERATED_DIR.mkdir(exist_ok=True)
(GENERATED_DIR / 'images').mkdir(exist_ok=True)
(GENERATED_DIR / 'audio').mkdir(exist_ok=True)
(GENERATED_DIR / 'videos').mkdir(exist_ok=True)

# Create the main app
app = FastAPI(title="KidsTube Creator API")

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ================== CONSTANTS ==================

# English-speaking countries targeting
ENGLISH_COUNTRIES = [
    {"code": "US", "name": "USA", "timezone": "America/New_York"},
    {"code": "GB", "name": "United Kingdom", "timezone": "Europe/London"},
    {"code": "CA", "name": "Canada", "timezone": "America/Toronto"},
    {"code": "AU", "name": "Australia", "timezone": "Australia/Sydney"},
    {"code": "IN", "name": "India", "timezone": "Asia/Kolkata"},
]

# Optimal posting hours (EST timezone as base)
POSTING_SCHEDULE_EST = [
    {"hour": 7, "type": "short", "target": "US_morning"},
    {"hour": 8, "type": "short", "target": "US_morning"},
    {"hour": 12, "type": "short", "target": "UK_evening"},
    {"hour": 14, "type": "short", "target": "global"},
    {"hour": 17, "type": "normal", "target": "US_evening"},
    {"hour": 19, "type": "short", "target": "US_evening"},
    {"hour": 21, "type": "short", "target": "AU_morning"},
]

# Short video templates
SHORT_TEMPLATES = [
    {
        "type": "colors",
        "hook": "Can you find the {color} color?",
        "themes": ["red", "blue", "green", "yellow", "purple", "orange", "pink"]
    },
    {
        "type": "counting",
        "hook": "Let's count to {number}!",
        "themes": ["5", "10", "3", "7"]
    },
    {
        "type": "alphabet",
        "hook": "What word starts with {letter}?",
        "themes": ["A", "B", "C", "D", "E", "F"]
    },
    {
        "type": "animals",
        "hook": "What sound does a {animal} make?",
        "themes": ["dog", "cat", "cow", "duck", "lion", "elephant"]
    },
    {
        "type": "shapes",
        "hook": "Can you find the {shape}?",
        "themes": ["circle", "square", "triangle", "star", "heart"]
    }
]

# ================== MODELS ==================

class Character(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str
    appearance: str
    personality: str
    voice_id: Optional[str] = None
    image_base64: Optional[str] = None
    channel_id: Optional[str] = None  # Belongs to a specific channel
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Series(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: str
    style: str
    target_age: str
    characters: List[str] = []
    youtube_channel_inspiration: Optional[str] = None
    channel_id: Optional[str] = None  # Belongs to a specific channel
    total_episodes: int = 0
    is_completed: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Video(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: str
    script: str
    scenes: List[dict] = []
    language: str = "en"
    duration_seconds: int = 0
    video_base64: Optional[str] = None
    thumbnail_base64: Optional[str] = None
    source_type: str = "manual"
    source_url: Optional[str] = None
    series_id: Optional[str] = None
    channel_id: Optional[str] = None  # Target channel for publishing
    video_type: str = "normal"  # normal or short
    status: str = "draft"
    youtube_video_id: Optional[str] = None
    youtube_url: Optional[str] = None
    published_at: Optional[datetime] = None
    scheduled_for: Optional[datetime] = None
    tags: List[str] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)

class YouTubeChannel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    channel_id: str
    channel_title: str
    channel_thumbnail: Optional[str] = None
    access_token: str
    refresh_token: str
    token_expiry: datetime
    # Auto-publishing settings
    auto_publish_enabled: bool = True
    shorts_per_day: int = 4
    videos_per_day: int = 1
    target_language: str = "en"
    target_countries: List[str] = ["US", "GB", "CA", "AU", "IN"]
    music_style: Optional[str] = None
    # Animation style setting
    animation_style: str = "3d_cocomelon"  # Default: 3D Cocomelon style
    # Stats
    videos_published_today: int = 0
    shorts_published_today: int = 0
    last_publish_date: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

# Animation styles available for channels
ANIMATION_STYLES = {
    "3d_cocomelon": {
        "name": "3D Cartoon (Cocomelon)",
        "description": "Style Cocomelon/BabyBus - 3D arrondi, couleurs vives",
        "prompt": """Create a 3D animated kids video in Cocomelon/BabyBus style:
- Bright colorful 3D cartoon animation with soft pastel and vibrant colors
- Soft rounded 3D characters with smooth plastic-like textures
- Big expressive cartoon eyes with sparkles
- Friendly smiling faces, cute button noses
- Simple clean environments with vibrant saturated colors
- Professional CGI quality like Cocomelon, BabyBus, Little Baby Bum
- Bright soft lighting, cheerful atmosphere for toddlers 0-5"""
    },
    "2d_gracies_corner": {
        "name": "2D Cartoon (Gracie's Corner)",
        "description": "Style Gracie's Corner - 2D coloré, personnages mignons",
        "prompt": """Create a 2D animated kids video in Gracie's Corner style:
- Bright colorful 2D cartoon animation
- Cute African American characters with big eyes and friendly faces
- Bold outlines and vibrant flat colors
- Modern 2D animation style with smooth movements
- Colorful backgrounds with simple shapes
- Educational nursery rhyme aesthetic
- Warm, inclusive, cheerful atmosphere for toddlers 0-5"""
    },
    "2d_classic": {
        "name": "2D Classique (Tom & Jerry)",
        "description": "Style classique - 2D traditionnel, animation fluide",
        "prompt": """Create a classic 2D animated kids video in Tom and Jerry/Looney Tunes style:
- Classic hand-drawn 2D animation style
- Expressive cartoon characters with exaggerated features
- Bold black outlines with vibrant colors
- Squash and stretch animation principles
- Simple painted backgrounds
- Classic cartoon aesthetic from golden age animation
- Fun, energetic, timeless cartoon style safe for children 0-5"""
    },
    "2d_anime": {
        "name": "2D Anime (Style japonais)",
        "description": "Style anime enfants - grands yeux, couleurs douces",
        "prompt": """Create a cute kids anime style 2D animation:
- Soft kawaii Japanese anime style for children
- Big sparkly anime eyes, small nose and mouth
- Soft pastel colors with gentle shading
- Chibi-style cute characters
- Clean line art with soft colors
- Magical girl/boy aesthetic safe for toddlers
- Gentle, sweet, dreamy atmosphere for children 0-5"""
    },
    "3d_pixar": {
        "name": "3D Pixar/Disney",
        "description": "Style Pixar - 3D cinématique, haute qualité",
        "prompt": """Create a Pixar/Disney quality 3D animated kids video:
- High quality CGI 3D animation like Pixar and Disney movies
- Realistic but stylized 3D characters with expressive faces
- Rich detailed environments with cinematic lighting
- Smooth realistic textures and materials
- Professional movie-quality rendering
- Warm emotional storytelling aesthetic
- Beautiful, magical atmosphere perfect for children 0-5"""
    }
}

class ScheduledPost(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    video_id: str
    channel_id: str
    scheduled_time: datetime
    video_type: str  # normal or short
    status: str = "pending"  # pending, publishing, published, failed
    retry_count: int = 0
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class PublishingQuota(BaseModel):
    channel_id: str
    date: str  # YYYY-MM-DD
    shorts_published: int = 0
    videos_published: int = 0
    shorts_target: int = 4
    videos_target: int = 1

class YouTubeVideoInfo(BaseModel):
    video_id: str
    title: str
    description: str
    channel_title: str
    view_count: int
    thumbnail_url: str

class GenerateVideoRequest(BaseModel):
    input_text: str
    input_type: str = "description"
    language: str = "en"
    max_duration_minutes: int = 3
    style: str = "cartoon"
    series_id: Optional[str] = None
    channel_id: Optional[str] = None
    video_type: str = "normal"

class GenerateShortRequest(BaseModel):
    template_type: str = "colors"  # colors, counting, alphabet, animals, shapes
    theme: Optional[str] = None
    language: str = "en"
    channel_id: Optional[str] = None

class TranslateRequest(BaseModel):
    video_id: str
    target_language: str

class PublishVideoRequest(BaseModel):
    video_id: str
    channel_id: str
    title: str
    description: str
    tags: List[str] = []
    privacy_status: str = "public"
    is_short: bool = False

class ChannelSettingsUpdate(BaseModel):
    auto_publish_enabled: Optional[bool] = None
    shorts_per_day: Optional[int] = None
    videos_per_day: Optional[int] = None
    target_language: Optional[str] = None
    music_style: Optional[str] = None
    animation_style: Optional[str] = None  # Animation style for this channel

class AskAssistantRequest(BaseModel):
    question: str
    context: Optional[str] = None

# ================== REFERENCE CHANNEL MODELS ==================

class ReferenceChannel(BaseModel):
    """A YouTube channel used as inspiration for content generation"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    youtube_channel_id: str  # The YouTube channel ID or URL
    youtube_channel_name: str
    youtube_channel_thumbnail: Optional[str] = None
    owner_channel_id: str  # The user's connected YouTube channel this reference belongs to
    analyzed: bool = False
    analysis_data: Optional[dict] = None  # Extracted themes, topics, style
    video_count_analyzed: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)

class ReferenceVideo(BaseModel):
    """A specific video used as reference"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    youtube_video_id: str
    youtube_video_url: str
    title: str
    description: str
    duration_seconds: int = 0
    owner_channel_id: str  # The user's connected YouTube channel
    reference_channel_id: Optional[str] = None
    analyzed: bool = False
    analysis_data: Optional[dict] = None  # Extracted episodes, themes, characters
    episodes_extracted: List[dict] = []  # List of episodes/segments found
    created_at: datetime = Field(default_factory=datetime.utcnow)

class VideoAnalysis(BaseModel):
    """Analysis results from a reference video"""
    themes: List[str] = []  # Educational themes found (colors, numbers, etc.)
    target_age: str = "2-5"
    style: str = "educational"
    characters_found: List[dict] = []  # Characters detected
    episodes: List[dict] = []  # Episode segments with timestamps
    educational_topics: List[str] = []
    mood: str = "cheerful"
    suggested_content: List[str] = []  # Content ideas inspired by this

class AddReferenceChannelRequest(BaseModel):
    youtube_channel_url: str
    owner_channel_id: str

class AddReferenceVideoRequest(BaseModel):
    youtube_video_url: str
    owner_channel_id: str

class GenerateFromReferenceRequest(BaseModel):
    reference_id: str  # Can be channel or video reference ID
    reference_type: str = "channel"  # "channel" or "video"
    owner_channel_id: str
    video_type: str = "short"  # "short" or "normal"
    language: str = "en"

# Kid-friendly voice IDs from ElevenLabs (for different character types)
KID_FRIENDLY_VOICES = [
    {"id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel", "type": "female_child", "description": "Young cheerful girl voice"},
    {"id": "AZnzlk1XvdvUeBnXmlld", "name": "Domi", "type": "female_young", "description": "Energetic young female"},
    {"id": "EXAVITQu4vr4xnSDxMaL", "name": "Bella", "type": "female_soft", "description": "Soft nurturing female"},
    {"id": "ErXwobaYiN019PkySvjV", "name": "Antoni", "type": "male_young", "description": "Friendly young male"},
    {"id": "MF3mGyEYCl7XYWbV9V6O", "name": "Elli", "type": "female_child", "description": "Playful child voice"},
    {"id": "TxGEqnHWrfWFTfGW9XjX", "name": "Josh", "type": "male_narrator", "description": "Warm narrator voice"},
]

# Character name generators for different types
CHARACTER_NAMES = {
    "toddler_boy": ["Tommy", "Benny", "Leo", "Max", "Ollie", "Teddy", "Charlie", "Milo"],
    "toddler_girl": ["Luna", "Bella", "Rosie", "Lily", "Emma", "Mia", "Sophie", "Daisy"],
    "animal_dog": ["Buddy", "Paws", "Spot", "Biscuit", "Woofy", "Barky"],
    "animal_cat": ["Whiskers", "Mittens", "Fluffy", "Snowball", "Cupcake"],
    "animal_bunny": ["Hoppy", "Cotton", "Carrot", "Flopsy", "Bouncy"],
    "animal_bird": ["Chirpy", "Tweet", "Sunny", "Feather", "Melody"],
    "robot": ["Robo", "Beepy", "Sparky", "Gizmo", "Bolt"],
}

# ================== YOUTUBE SERVICE ==================

async def search_youtube_kids_videos(query: str = "kids educational", min_views: int = 1000000, max_results: int = 10):
    """Search for popular kids videos on YouTube"""
    try:
        async with httpx.AsyncClient() as client:
            params = {
                'part': 'snippet',
                'q': f'{query} for kids children',
                'type': 'video',
                'videoCategoryId': '1',
                'maxResults': max_results,
                'order': 'viewCount',
                'key': YOUTUBE_API_KEY
            }
            response = await client.get('https://www.googleapis.com/youtube/v3/search', params=params)
            
            if response.status_code != 200:
                logger.error(f"YouTube API error: {response.text}")
                return []
            
            data = response.json()
            video_ids = [item['id']['videoId'] for item in data.get('items', []) if 'videoId' in item.get('id', {})]
            
            if not video_ids:
                return []
            
            stats_params = {
                'part': 'statistics,snippet',
                'id': ','.join(video_ids),
                'key': YOUTUBE_API_KEY
            }
            stats_response = await client.get('https://www.googleapis.com/youtube/v3/videos', params=stats_params)
            
            if stats_response.status_code != 200:
                return []
            
            stats_data = stats_response.json()
            
            videos = []
            for item in stats_data.get('items', []):
                view_count = int(item['statistics'].get('viewCount', 0))
                if view_count >= min_views:
                    videos.append(YouTubeVideoInfo(
                        video_id=item['id'],
                        title=item['snippet']['title'],
                        description=item['snippet']['description'],
                        channel_title=item['snippet']['channelTitle'],
                        view_count=view_count,
                        thumbnail_url=item['snippet']['thumbnails']['high']['url']
                    ))
            
            return videos
    except Exception as e:
        logger.error(f"Error searching YouTube: {e}")
        return []

async def get_channel_videos(channel_id: str, max_results: int = 20):
    """Get videos from a specific YouTube channel"""
    try:
        async with httpx.AsyncClient() as client:
            channel_params = {
                'part': 'contentDetails',
                'id': channel_id,
                'key': YOUTUBE_API_KEY
            }
            
            if 'youtube.com' in channel_id:
                if '@' in channel_id:
                    username = channel_id.split('@')[-1].split('/')[0].split('?')[0]
                    channel_params = {
                        'part': 'contentDetails',
                        'forHandle': username,
                        'key': YOUTUBE_API_KEY
                    }
                elif '/channel/' in channel_id:
                    channel_id = channel_id.split('/channel/')[-1].split('/')[0]
                    channel_params['id'] = channel_id
            
            channel_response = await client.get('https://www.googleapis.com/youtube/v3/channels', params=channel_params)
            
            if channel_response.status_code != 200:
                logger.error(f"Channel API error: {channel_response.text}")
                return []
            
            channel_data = channel_response.json()
            if not channel_data.get('items'):
                return []
            
            uploads_playlist = channel_data['items'][0]['contentDetails']['relatedPlaylists']['uploads']
            
            playlist_params = {
                'part': 'snippet',
                'playlistId': uploads_playlist,
                'maxResults': max_results,
                'key': YOUTUBE_API_KEY
            }
            playlist_response = await client.get('https://www.googleapis.com/youtube/v3/playlistItems', params=playlist_params)
            
            if playlist_response.status_code != 200:
                return []
            
            playlist_data = playlist_response.json()
            video_ids = [item['snippet']['resourceId']['videoId'] for item in playlist_data.get('items', [])]
            
            if video_ids:
                stats_params = {
                    'part': 'statistics,snippet',
                    'id': ','.join(video_ids),
                    'key': YOUTUBE_API_KEY
                }
                stats_response = await client.get('https://www.googleapis.com/youtube/v3/videos', params=stats_params)
                
                if stats_response.status_code == 200:
                    stats_data = stats_response.json()
                    videos = []
                    for item in stats_data.get('items', []):
                        videos.append(YouTubeVideoInfo(
                            video_id=item['id'],
                            title=item['snippet']['title'],
                            description=item['snippet']['description'],
                            channel_title=item['snippet']['channelTitle'],
                            view_count=int(item['statistics'].get('viewCount', 0)),
                            thumbnail_url=item['snippet']['thumbnails']['high']['url']
                        ))
                    return videos
            
            return []
    except Exception as e:
        logger.error(f"Error getting channel videos: {e}")
        return []

# ================== LLM SERVICE ==================

async def generate_script(prompt: str, language: str = "en", max_scenes: int = 8):
    """Generate a kids video script using LLM for ages 0-5"""
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        
        language_names = {"en": "English", "fr": "French", "es": "Spanish"}
        lang_name = language_names.get(language, "English")
        
        system_prompt = f"""You are a professional children's content creator specializing in content for BABIES and TODDLERS (ages 0-5 years old).

TARGET AUDIENCE: Babies (0-2) and Toddlers (2-5)

CRITICAL RULES FOR 0-5 YEARS:
1. Use VERY SIMPLE words (1-2 syllables max)
2. SLOW pace - babies need time to process
3. LOTS of REPETITION - babies LOVE repetition
4. Bright, simple visuals - one main focus per scene
5. Simple sentences (3-5 words max)
6. Educational basics: colors, numbers 1-5, shapes, animals sounds
7. Cheerful, gentle, soothing tone
8. NO scary elements, NO conflict
9. Write in {lang_name}
10. Create exactly {max_scenes} scenes
11. Each scene: 10-15 seconds, very simple

STYLE: Like Cocomelon, BabyBus, Little Baby Bum
- Nursery rhyme style
- Sing-song narration
- Very repetitive structure
- Simple concepts (one per video)

Return ONLY valid JSON:
{{
    "title": "Simple 3-4 word title",
    "description": "Brief description with hashtags",
    "target_age": "0-5",
    "scenes": [
        {{
            "scene_number": 1,
            "narration": "Very simple text (5 words max)",
            "visual_description": "Simple visual with bright colors",
            "duration_seconds": 10
        }}
    ]
}}"""

        llm = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=str(uuid.uuid4()),
            system_message=system_prompt
        )
        
        response = await llm.send_message(UserMessage(text=f"Create a simple video for babies and toddlers about: {prompt}"))
        
        response_text = response.strip()
        if response_text.startswith('```'):
            response_text = response_text.split('```')[1]
            if response_text.startswith('json'):
                response_text = response_text[4:]
        
        script_data = json.loads(response_text)
        return script_data
        
    except Exception as e:
        logger.error(f"Error generating script: {e}")
        raise HTTPException(status_code=500, detail=f"Script generation failed: {str(e)}")

async def generate_short_script(template_type: str, theme: str, language: str = "en"):
    """Generate a viral Short script for babies and toddlers (0-5 years)"""
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        
        language_names = {"en": "English", "fr": "French", "es": "Spanish"}
        lang_name = language_names.get(language, "English")
        
        template = next((t for t in SHORT_TEMPLATES if t["type"] == template_type), SHORT_TEMPLATES[0])
        hook = template["hook"].format(**{template_type.rstrip('s'): theme, "color": theme, "number": theme, "letter": theme, "animal": theme, "shape": theme})
        
        system_prompt = f"""You are a YouTube Shorts creator for BABIES and TODDLERS (0-5 years old).
Create VERY SIMPLE, repetitive, educational content like Cocomelon or BabyBus.

TARGET AUDIENCE: Babies (0-2) and Toddlers (2-5)

VIRAL SHORT STRUCTURE FOR BABIES:
1. HOOK (0-3 seconds): Simple question with bright visual
2. LEARNING (4-20 seconds): Show and repeat the concept 3 times
3. CELEBRATION (20-30 seconds): Happy ending, encourage to watch again

CRITICAL RULES FOR 0-5 YEARS:
- VERY simple words (1-2 syllables)
- REPEAT everything 2-3 times
- SLOW, gentle pace
- Simple sentences (3-5 words MAX)
- ONE concept only (one color, one number, etc.)
- Cheerful, soothing voice
- Write in {lang_name}
- Total: 15-35 seconds

Return ONLY valid JSON:
{{
    "title": "Simple 3-word title",
    "description": "Description with baby/toddler hashtags",
    "target_age": "0-5",
    "hook": "Simple question",
    "scenes": [
        {{
            "scene_number": 1,
            "narration": "Simple text (5 words max)",
            "visual_description": "Bright colorful visual for babies",
            "duration_seconds": 3
        }},
        {{
            "scene_number": 2,
            "narration": "Teaching with repetition",
            "visual_description": "Simple focused visual",
            "duration_seconds": 15
        }},
        {{
            "scene_number": 3,
            "narration": "Happy celebration (Yay! Good job!)",
            "visual_description": "Cheerful celebration visual",
            "duration_seconds": 10
        }}
    ],
    "tags": ["baby", "toddler", "learning", "0-5", "educational"]
}}"""

        llm = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=str(uuid.uuid4()),
            system_message=system_prompt
        )
        
        prompt = f"Create a simple Short for babies (0-5 years) about: {hook}. Theme: {theme}. Make it very simple, repetitive, and educational for toddlers!"
        response = await llm.send_message(UserMessage(text=prompt))
        
        response_text = response.strip()
        if response_text.startswith('```'):
            response_text = response_text.split('```')[1]
            if response_text.startswith('json'):
                response_text = response_text[4:]
        
        script_data = json.loads(response_text)
        return script_data
        
    except Exception as e:
        logger.error(f"Error generating short script: {e}")
        raise HTTPException(status_code=500, detail=f"Short script generation failed: {str(e)}")

async def generate_optimized_metadata(video_title: str, video_description: str, language: str = "en"):
    """Generate YouTube-optimized title, description, and tags for maximum views"""
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        
        system_prompt = """You are a YouTube SEO expert specializing in kids content. Your job is to optimize video metadata for MAXIMUM VIEWS.

Rules for kids videos:
1. TITLE: 3-5 words max, include power words, no clickbait
2. DESCRIPTION: Include keywords naturally, add timestamps, use hashtags
3. TAGS: Mix broad and specific tags, include trending topics

For English content targeting: USA, UK, Canada, Australia, India

Return ONLY valid JSON:
{
    "optimized_title": "Short catchy title",
    "optimized_description": "Full description with hashtags and keywords",
    "tags": ["tag1", "tag2", ...],
    "hashtags": ["#kidsvideo", "#learning", ...]
}"""

        llm = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=str(uuid.uuid4()),
            system_message=system_prompt
        )
        
        response = await llm.send_message(UserMessage(
            text=f"Optimize this kids video for YouTube:\nTitle: {video_title}\nDescription: {video_description}\nLanguage: {language}"
        ))
        
        response_text = response.strip()
        if response_text.startswith('```'):
            response_text = response_text.split('```')[1]
            if response_text.startswith('json'):
                response_text = response_text[4:]
        
        return json.loads(response_text)
        
    except Exception as e:
        logger.error(f"Error generating metadata: {e}")
        return {
            "optimized_title": video_title,
            "optimized_description": video_description,
            "tags": ["kids", "children", "learning", "fun", "educational"],
            "hashtags": ["#kidsvideo", "#learning", "#fun"]
        }

async def translate_text(text: str, target_language: str):
    """Translate text to another language"""
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        
        language_names = {"en": "English", "fr": "French", "es": "Spanish"}
        target_lang_name = language_names.get(target_language, "English")
        
        llm = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=str(uuid.uuid4()),
            system_message=f"You are a translator. Translate the following text to {target_lang_name}. Keep the same tone and style, appropriate for children. Return ONLY the translated text, nothing else."
        )
        
        response = await llm.send_message(UserMessage(text=text))
        
        return response.strip()
        
    except Exception as e:
        logger.error(f"Error translating: {e}")
        return text

async def ask_assistant(question: str, context: str = ""):
    """AI Assistant to answer user questions about the app"""
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        
        system_prompt = """You are a helpful assistant for the KidsTube Creator app. Answer questions in a friendly, clear way.

About KidsTube Creator:
- Creates AI-generated videos for children's YouTube channels
- Generates scripts, images, and voiceovers automatically
- Supports YouTube Shorts (15-35 seconds) and normal videos (1-5 minutes)
- Auto-publishes to multiple YouTube channels
- Targets English-speaking countries (USA, UK, Canada, Australia, India)
- Optimal posting: 4 Shorts + 1 video per day per channel
- Best posting times (EST): 7-9am, 12-2pm, 5-9pm

Features:
1. Create Video: Generate full videos from text descriptions
2. Create Shorts: Viral 15-35 second videos with hook-action-reveal structure
3. Series & Characters: Create ongoing series with recurring characters
4. Auto Publishing: Automatic daily posting to connected YouTube channels
5. Multi-Channel: Manage multiple YouTube channels independently
6. SEO Optimization: Automatic title/description/tag optimization

Answer in the user's language (French if they ask in French, etc.)"""

        llm = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=str(uuid.uuid4()),
            system_message=system_prompt
        )
        
        full_question = question
        if context:
            full_question = f"Context: {context}\n\nQuestion: {question}"
        
        response = await llm.send_message(UserMessage(text=full_question))
        return response.strip()
        
    except Exception as e:
        logger.error(f"Error with assistant: {e}")
        return "Je suis désolé, je n'ai pas pu traiter votre question. Veuillez réessayer."

# ================== VIDEO ANALYSIS SERVICE ==================

async def analyze_youtube_video_content(video_id: str, video_title: str, video_description: str):
    """Analyze a YouTube video to extract themes, characters, and episode structure"""
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        
        system_prompt = """You are an expert at analyzing children's educational videos.
Your job is to extract:
1. Educational themes and topics
2. Character types (not exact names - we'll create new ones)
3. Episode structure and segments
4. Teaching methods used
5. Visual style description

Return ONLY valid JSON with this structure:
{
    "themes": ["colors", "numbers", "alphabet"],
    "educational_topics": ["learning colors", "counting 1-10"],
    "character_types": [
        {"type": "toddler_boy", "role": "main_character", "description": "curious child who learns"},
        {"type": "animal_dog", "role": "helper", "description": "friendly pet that helps"}
    ],
    "episodes": [
        {"title": "Learning Red", "theme": "colors", "duration_estimate": 60, "summary": "Character learns about red color"}
    ],
    "style": "3D animated preschool cartoon",
    "mood": "cheerful educational",
    "target_age": "2-5",
    "suggested_original_content": [
        "A new character learns about the color blue with animal friends",
        "Counting adventure with a robot helper"
    ]
}"""

        llm = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=str(uuid.uuid4()),
            system_message=system_prompt
        )
        
        prompt = f"""Analyze this children's video and extract educational content structure:

Title: {video_title}
Description: {video_description}

Extract the themes, character types (NOT exact names), episode segments, and suggest original content we can create inspired by this video's educational approach.

Remember: We want to CREATE ORIGINAL CONTENT inspired by the educational themes, not copy the video."""

        response = await llm.send_message(UserMessage(text=prompt))
        
        response_text = response.strip()
        if response_text.startswith('```'):
            response_text = response_text.split('```')[1]
            if response_text.startswith('json'):
                response_text = response_text[4:]
        
        analysis = json.loads(response_text)
        return analysis
        
    except Exception as e:
        logger.error(f"Error analyzing video: {e}")
        return {
            "themes": ["educational"],
            "educational_topics": ["general learning"],
            "character_types": [{"type": "toddler_boy", "role": "main", "description": "curious learner"}],
            "episodes": [],
            "style": "3D animated",
            "mood": "cheerful",
            "target_age": "2-5",
            "suggested_original_content": []
        }

async def analyze_youtube_channel_content(channel_id: str, videos: list):
    """Analyze multiple videos from a channel to understand its style and themes"""
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        
        # Compile video info
        video_summaries = "\n".join([
            f"- {v.title} ({v.view_count} views)" for v in videos[:20]
        ])
        
        system_prompt = """You are an expert at analyzing children's YouTube channels.
Analyze the channel's content strategy and extract:
1. Main educational themes
2. Character style patterns
3. Video format patterns
4. What makes this channel successful

Return ONLY valid JSON:
{
    "main_themes": ["colors", "numbers", "nursery rhymes"],
    "content_style": "Preschool educational with singing and animation",
    "character_style": "Cute 3D toddler characters with animal friends",
    "format_patterns": ["song-based learning", "repetitive structure", "colorful visuals"],
    "success_factors": ["consistent characters", "catchy music", "bright colors"],
    "suggested_series": [
        {"title": "Color Adventures", "theme": "colors", "episodes": 10},
        {"title": "Number Friends", "theme": "counting", "episodes": 10}
    ],
    "recommended_video_length": {"shorts": 30, "normal": 180}
}"""

        llm = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=str(uuid.uuid4()),
            system_message=system_prompt
        )
        
        prompt = f"""Analyze this children's YouTube channel based on its videos:

Videos from channel:
{video_summaries}

What educational themes and content patterns does this channel use? 
What original content series could we create inspired by their approach?"""

        response = await llm.send_message(UserMessage(text=prompt))
        
        response_text = response.strip()
        if response_text.startswith('```'):
            response_text = response_text.split('```')[1]
            if response_text.startswith('json'):
                response_text = response_text[4:]
        
        return json.loads(response_text)
        
    except Exception as e:
        logger.error(f"Error analyzing channel: {e}")
        return {
            "main_themes": ["educational"],
            "content_style": "Children's educational",
            "character_style": "Cute animated characters",
            "format_patterns": [],
            "success_factors": [],
            "suggested_series": [],
            "recommended_video_length": {"shorts": 30, "normal": 180}
        }

async def generate_original_content_from_reference(analysis: dict, language: str = "en"):
    """Generate completely original content inspired by reference analysis"""
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        
        themes = analysis.get("themes", ["colors"])
        character_types = analysis.get("character_types", [])
        
        # Generate new character names
        new_characters = []
        for char_type in character_types:
            char_category = char_type.get("type", "toddler_boy")
            available_names = CHARACTER_NAMES.get(char_category, CHARACTER_NAMES["toddler_boy"])
            new_name = random.choice(available_names)
            new_characters.append({
                "name": new_name,
                "type": char_category,
                "role": char_type.get("role", "main"),
                "description": char_type.get("description", ""),
                "voice_id": random.choice(KID_FRIENDLY_VOICES)["id"]
            })
        
        system_prompt = f"""You are a children's content creator. Create an ORIGINAL educational video script.

IMPORTANT RULES:
1. Create 100% ORIGINAL content - do not copy any existing videos
2. Use ONLY these character names: {[c['name'] for c in new_characters]}
3. Make it educational and fun for toddlers (ages 2-5)
4. Write in {language}
5. Structure: Introduction → Teaching → Practice → Celebration

Return ONLY valid JSON:
{{
    "title": "Original catchy title (3-4 words)",
    "description": "SEO optimized description",
    "characters": [
        {{"name": "CharName", "description": "visual appearance for consistent animation"}}
    ],
    "scenes": [
        {{
            "scene_number": 1,
            "narration": "What character says",
            "visual_description": "Detailed scene description for 3D animation",
            "duration_seconds": 5,
            "educational_element": "What child learns"
        }}
    ],
    "tags": ["kids", "learning", "educational"]
}}"""

        llm = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=str(uuid.uuid4()),
            system_message=system_prompt
        )
        
        theme = random.choice(themes)
        prompt = f"""Create an ORIGINAL educational video about: {theme}

Use these characters: {new_characters}

Make it fun, educational, and perfect for toddlers. Create 4-6 scenes.
The content must be completely original - inspired by educational approach but NOT copying any existing videos."""

        response = await llm.send_message(UserMessage(text=prompt))
        
        response_text = response.strip()
        if response_text.startswith('```'):
            response_text = response_text.split('```')[1]
            if response_text.startswith('json'):
                response_text = response_text[4:]
        
        script_data = json.loads(response_text)
        script_data["generated_characters"] = new_characters
        
        return script_data
        
    except Exception as e:
        logger.error(f"Error generating from reference: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ================== IMAGE SERVICE ==================

async def generate_scene_image(visual_description: str, style: str = "3d_cocomelon", is_short: bool = False, character_info: str = ""):
    """Generate an image for a scene using the channel's animation style"""
    try:
        from emergentintegrations.llm.openai.image_generation import OpenAIImageGeneration
        
        aspect = "vertical, 9:16 aspect ratio" if is_short else "horizontal, 16:9 aspect ratio"
        
        # Get style prompt from ANIMATION_STYLES
        style_config = ANIMATION_STYLES.get(style, ANIMATION_STYLES["3d_cocomelon"])
        style_prompt = style_config["prompt"]
        
        # Add character consistency if provided
        character_prompt = ""
        if character_info:
            character_prompt = f"\nCharacter description (MUST match exactly): {character_info}\n"
        
        prompt = f"""{style_prompt}
{character_prompt}
Scene to illustrate ({aspect}):
{visual_description}

IMPORTANT: Content for babies and toddlers ages 0-5. Safe, educational, cheerful."""
        
        img_gen = OpenAIImageGeneration(api_key=EMERGENT_LLM_KEY)
        images = await img_gen.generate_images(
            prompt=prompt,
            model="gpt-image-1",
            number_of_images=1,
            quality="low"
        )
        
        if images:
            image_b64 = base64.b64encode(images[0]).decode()
            return image_b64
        
        return None
        
    except Exception as e:
        logger.error(f"Error generating image: {e}")
        return None

# ================== VOICE SERVICE ==================

async def generate_voice(text: str, voice_id: str = "21m00Tcm4TlvDq8ikWAM"):
    """Generate voice using ElevenLabs"""
    try:
        from elevenlabs import ElevenLabs
        
        client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        
        audio_generator = client.text_to_speech.convert(
            text=text,
            voice_id=voice_id,
            model_id="eleven_multilingual_v2"
        )
        
        audio_data = b""
        for chunk in audio_generator:
            audio_data += chunk
        
        audio_b64 = base64.b64encode(audio_data).decode()
        return audio_b64
        
    except Exception as e:
        logger.error(f"Error generating voice: {e}")
        return None

async def get_available_voices():
    """Get list of available voices from ElevenLabs"""
    try:
        from elevenlabs import ElevenLabs
        
        client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        voices_response = client.voices.get_all()
        
        voices = []
        for voice in voices_response.voices:
            voices.append({
                "voice_id": voice.voice_id,
                "name": voice.name,
                "category": getattr(voice, 'category', 'general'),
                "labels": getattr(voice, 'labels', {})
            })
        
        return voices
    except Exception as e:
        logger.error(f"Error getting voices: {e}")
        return []

# ================== AUTO-PUBLISHING SERVICE ==================

async def get_or_create_daily_quota(channel_id: str) -> dict:
    """Get or create publishing quota for today"""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    
    quota = await db.publishing_quotas.find_one({
        "channel_id": channel_id,
        "date": today
    })
    
    if not quota:
        # Get channel settings
        channel = await db.youtube_channels.find_one({"id": channel_id})
        quota = {
            "channel_id": channel_id,
            "date": today,
            "shorts_published": 0,
            "videos_published": 0,
            "shorts_target": channel.get("shorts_per_day", 4) if channel else 4,
            "videos_target": channel.get("videos_per_day", 1) if channel else 1
        }
        await db.publishing_quotas.insert_one(quota)
    
    return quota

async def schedule_video_for_channel(video_id: str, channel_id: str, video_type: str = "short"):
    """Schedule a video for optimal posting time"""
    now = datetime.utcnow()
    
    # Find next available slot
    scheduled_posts = await db.scheduled_posts.find({
        "channel_id": channel_id,
        "status": "pending",
        "scheduled_time": {"$gt": now}
    }).to_list(100)
    
    scheduled_hours = [p["scheduled_time"].hour for p in scheduled_posts]
    
    # Find optimal time slot
    for schedule in POSTING_SCHEDULE_EST:
        if schedule["type"] == video_type and schedule["hour"] not in scheduled_hours:
            # Schedule for today or tomorrow
            scheduled_time = now.replace(hour=schedule["hour"], minute=0, second=0, microsecond=0)
            if scheduled_time <= now:
                scheduled_time += timedelta(days=1)
            
            post = ScheduledPost(
                video_id=video_id,
                channel_id=channel_id,
                scheduled_time=scheduled_time,
                video_type=video_type
            )
            await db.scheduled_posts.insert_one(post.dict())
            
            # Update video with scheduled time
            await db.videos.update_one(
                {"id": video_id},
                {"$set": {"scheduled_for": scheduled_time}}
            )
            
            return post.dict()
    
    # If no slot available, schedule for next day first slot
    scheduled_time = (now + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
    post = ScheduledPost(
        video_id=video_id,
        channel_id=channel_id,
        scheduled_time=scheduled_time,
        video_type=video_type
    )
    await db.scheduled_posts.insert_one(post.dict())
    return post.dict()

async def auto_generate_content_for_channel(channel_id: str, background_tasks: BackgroundTasks):
    """Generate daily content for a specific channel"""
    channel = await db.youtube_channels.find_one({"id": channel_id})
    if not channel or not channel.get("auto_publish_enabled", True):
        return {"message": "Auto-publish disabled for this channel"}
    
    quota = await get_or_create_daily_quota(channel_id)
    
    shorts_needed = quota["shorts_target"] - quota["shorts_published"]
    videos_needed = quota["videos_target"] - quota["videos_published"]
    
    generated = {"shorts": 0, "videos": 0}
    
    # Generate Shorts
    for _ in range(shorts_needed):
        template = random.choice(SHORT_TEMPLATES)
        theme = random.choice(template["themes"])
        
        try:
            script_data = await generate_short_script(
                template["type"],
                theme,
                channel.get("target_language", "en")
            )
            
            video_id = str(uuid.uuid4())
            video = Video(
                id=video_id,
                title=script_data.get("title", "Kids Short"),
                description=script_data.get("description", ""),
                script=json.dumps(script_data),
                language=channel.get("target_language", "en"),
                video_type="short",
                channel_id=channel_id,
                status="generating",
                tags=script_data.get("tags", [])
            )
            
            await db.videos.insert_one(video.dict())
            
            background_tasks.add_task(
                process_video_generation,
                video_id,
                script_data,
                "cartoon",
                True  # is_short
            )
            
            # Schedule for publishing
            await schedule_video_for_channel(video_id, channel_id, "short")
            
            generated["shorts"] += 1
            
        except Exception as e:
            logger.error(f"Error generating short: {e}")
    
    # Generate normal video
    for _ in range(videos_needed):
        # Get trending for inspiration
        trending = await search_youtube_kids_videos("kids cartoon educational", min_views=500000, max_results=5)
        
        if trending:
            inspiration = random.choice(trending)
            prompt = f"Create an original educational video for kids inspired by: {inspiration.title}"
        else:
            themes = ["learning colors", "counting numbers", "animal sounds", "alphabet fun", "shapes adventure"]
            prompt = f"Create an educational video about: {random.choice(themes)}"
        
        try:
            script_data = await generate_script(
                prompt,
                channel.get("target_language", "en"),
                max_scenes=6
            )
            
            video_id = str(uuid.uuid4())
            video = Video(
                id=video_id,
                title=script_data.get("title", "Kids Video"),
                description=script_data.get("description", ""),
                script=json.dumps(script_data),
                language=channel.get("target_language", "en"),
                video_type="normal",
                channel_id=channel_id,
                status="generating"
            )
            
            await db.videos.insert_one(video.dict())
            
            background_tasks.add_task(
                process_video_generation,
                video_id,
                script_data,
                "cartoon",
                False
            )
            
            # Schedule for publishing
            await schedule_video_for_channel(video_id, channel_id, "normal")
            
            generated["videos"] += 1
            
        except Exception as e:
            logger.error(f"Error generating video: {e}")
    
    return generated

async def process_scheduled_posts(background_tasks: BackgroundTasks):
    """Process all pending scheduled posts"""
    now = datetime.utcnow()
    
    pending_posts = await db.scheduled_posts.find({
        "status": "pending",
        "scheduled_time": {"$lte": now}
    }).to_list(100)
    
    results = []
    
    for post in pending_posts:
        try:
            # Update status to publishing
            await db.scheduled_posts.update_one(
                {"id": post["id"]},
                {"$set": {"status": "publishing"}}
            )
            
            # Get video and channel
            video = await db.videos.find_one({"id": post["video_id"]})
            channel = await db.youtube_channels.find_one({"id": post["channel_id"]})
            
            if not video or not channel:
                await db.scheduled_posts.update_one(
                    {"id": post["id"]},
                    {"$set": {"status": "failed", "error_message": "Video or channel not found"}}
                )
                continue
            
            if video["status"] != "completed":
                # Reschedule for later
                new_time = now + timedelta(hours=1)
                await db.scheduled_posts.update_one(
                    {"id": post["id"]},
                    {"$set": {"status": "pending", "scheduled_time": new_time}}
                )
                continue
            
            # Optimize metadata
            metadata = await generate_optimized_metadata(
                video["title"],
                video.get("description", ""),
                video.get("language", "en")
            )
            
            # Check if token needs refresh
            if datetime.utcnow() >= channel["token_expiry"]:
                access_token = await refresh_youtube_token(post["channel_id"])
            else:
                access_token = channel["access_token"]
            
            # Publish
            background_tasks.add_task(
                process_youtube_publish,
                video,
                channel,
                access_token,
                metadata["optimized_title"],
                metadata["optimized_description"],
                metadata["tags"],
                "public",
                post["id"]
            )
            
            results.append({"post_id": post["id"], "status": "publishing"})
            
        except Exception as e:
            logger.error(f"Error processing scheduled post: {e}")
            await db.scheduled_posts.update_one(
                {"id": post["id"]},
                {"$set": {
                    "status": "failed",
                    "error_message": str(e),
                    "retry_count": post.get("retry_count", 0) + 1
                }}
            )
    
    return results

# ================== API ENDPOINTS ==================

@api_router.get("/")
async def root():
    return {"message": "KidsTube Creator API", "version": "2.0"}

@api_router.get("/health")
async def health_check():
    # Test MongoDB connection
    mongo_status = "unknown"
    mongo_error = None
    try:
        result = await db.command("ping")
        mongo_status = "connected" if result.get("ok") == 1.0 else "error"
    except Exception as e:
        mongo_status = "error"
        mongo_error = str(e)
    
    return {
        "status": "healthy" if mongo_status == "connected" else "degraded",
        "youtube_api": bool(YOUTUBE_API_KEY),
        "elevenlabs_api": bool(ELEVENLABS_API_KEY),
        "emergent_api": bool(EMERGENT_LLM_KEY),
        "mongodb": mongo_status,
        "mongodb_error": mongo_error,
        "mongo_url_type": "srv" if "mongodb+srv" in mongo_url else "standard"
    }


@api_router.post("/admin/cleanup")
async def cleanup_database():
    """Clean up database to free space - remove failed/old videos and keep only latest"""
    try:
        # Count before cleanup
        total_before = await db.videos.count_documents({})
        
        # 1. Delete all failed videos
        failed = await db.videos.delete_many({"status": "failed"})
        
        # 2. Delete videos without video_url (incomplete)
        incomplete = await db.videos.delete_many({"video_url": {"$exists": False}})
        
        # 3. Delete generating videos older than 1 hour (stuck)
        stuck_cutoff = datetime.utcnow() - timedelta(hours=1)
        stuck = await db.videos.delete_many({
            "status": "generating",
            "created_at": {"$lt": stuck_cutoff}
        })
        
        # 4. For completed videos, remove large base64 data to save space
        # Keep only the video URL, not the raw data
        await db.videos.update_many(
            {"status": "completed"},
            {"$unset": {"video_data": "", "audio_data": "", "image_data": ""}}
        )
        
        # 5. Clean up old scheduled posts
        await db.scheduled_posts.delete_many({"status": {"$in": ["failed", "cancelled"]}})
        
        # 6. Clean old daily quotas
        old_date = datetime.utcnow() - timedelta(days=2)
        await db.daily_quotas.delete_many({"date": {"$lt": old_date.strftime("%Y-%m-%d")}})
        
        total_after = await db.videos.count_documents({})
        
        return {
            "message": "Cleanup complete",
            "videos_before": total_before,
            "videos_after": total_after,
            "failed_deleted": failed.deleted_count,
            "incomplete_deleted": incomplete.deleted_count,
            "stuck_deleted": stuck.deleted_count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.delete("/admin/reset-all")
async def reset_all_content():
    """Reset all generated content but keep channel connections"""
    try:
        vids = await db.videos.delete_many({})
        posts = await db.scheduled_posts.delete_many({})
        quotas = await db.daily_quotas.delete_many({})
        return {
            "message": "All content reset",
            "videos_deleted": vids.deleted_count,
            "posts_deleted": posts.deleted_count,
            "quotas_deleted": quotas.deleted_count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ---- Animation Styles Endpoint ----

@api_router.get("/animation-styles")
async def get_animation_styles():
    """Get all available animation styles for channels"""
    styles = []
    for style_id, style_config in ANIMATION_STYLES.items():
        styles.append({
            "id": style_id,
            "name": style_config["name"],
            "description": style_config["description"]
        })
    return {"styles": styles}

# ---- Assistant Endpoint ----

@api_router.post("/assistant/ask")
async def ask_ai_assistant(request: AskAssistantRequest):
    """Ask the AI assistant about how to use the app"""
    response = await ask_assistant(request.question, request.context or "")
    return {"answer": response}

# ---- YouTube Endpoints ----

@api_router.get("/youtube/trending")
async def get_trending_kids_videos(query: str = "kids cartoon", min_views: int = 1000000):
    """Get trending kids videos with 1M+ views"""
    videos = await search_youtube_kids_videos(query, min_views)
    return {"videos": [v.dict() for v in videos]}

@api_router.post("/youtube/analyze-channel")
async def analyze_youtube_channel(channel_url: str):
    """Analyze a YouTube channel and get its popular videos"""
    videos = await get_channel_videos(channel_url)
    return {"videos": [v.dict() for v in videos], "count": len(videos)}

# ---- Reference Channels & Videos Endpoints ----

@api_router.post("/references/channel")
async def add_reference_channel(request: AddReferenceChannelRequest, background_tasks: BackgroundTasks):
    """Add a YouTube channel as a reference for content inspiration"""
    try:
        # Get channel info
        videos = await get_channel_videos(request.youtube_channel_url, max_results=20)
        
        if not videos:
            raise HTTPException(status_code=400, detail="Could not fetch channel videos")
        
        channel_name = videos[0].channel_title if videos else "Unknown Channel"
        
        # Create reference channel entry
        ref_channel = ReferenceChannel(
            youtube_channel_id=request.youtube_channel_url,
            youtube_channel_name=channel_name,
            owner_channel_id=request.owner_channel_id,
            analyzed=False
        )
        
        await db.reference_channels.insert_one(ref_channel.dict())
        
        # Start analysis in background
        background_tasks.add_task(
            analyze_and_store_channel_reference,
            ref_channel.id,
            videos
        )
        
        return {
            "reference_id": ref_channel.id,
            "channel_name": channel_name,
            "videos_found": len(videos),
            "status": "analyzing"
        }
        
    except Exception as e:
        logger.error(f"Error adding reference channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/references/video")
async def add_reference_video(request: AddReferenceVideoRequest, background_tasks: BackgroundTasks):
    """Add a specific YouTube video as a reference"""
    try:
        # Extract video ID from URL
        video_id = None
        if "youtube.com/watch?v=" in request.youtube_video_url:
            video_id = request.youtube_video_url.split("v=")[1].split("&")[0]
        elif "youtu.be/" in request.youtube_video_url:
            video_id = request.youtube_video_url.split("youtu.be/")[1].split("?")[0]
        
        if not video_id:
            raise HTTPException(status_code=400, detail="Invalid YouTube URL")
        
        # Get video info from YouTube API
        async with httpx.AsyncClient() as client:
            params = {
                'part': 'snippet,contentDetails,statistics',
                'id': video_id,
                'key': YOUTUBE_API_KEY
            }
            response = await client.get('https://www.googleapis.com/youtube/v3/videos', params=params)
            
            if response.status_code != 200:
                raise HTTPException(status_code=400, detail="Could not fetch video info")
            
            data = response.json()
            if not data.get('items'):
                raise HTTPException(status_code=404, detail="Video not found")
            
            video_info = data['items'][0]
            snippet = video_info['snippet']
            
            # Parse duration
            duration_str = video_info['contentDetails']['duration']
            # Parse ISO 8601 duration (simplified)
            duration_seconds = 0
            if 'H' in duration_str:
                hours = int(duration_str.split('H')[0].replace('PT', ''))
                duration_seconds += hours * 3600
            if 'M' in duration_str:
                mins_part = duration_str.split('M')[0]
                if 'H' in mins_part:
                    mins_part = mins_part.split('H')[1]
                else:
                    mins_part = mins_part.replace('PT', '')
                duration_seconds += int(mins_part) * 60
            if 'S' in duration_str:
                secs_part = duration_str.split('S')[0].split('M')[-1]
                if secs_part:
                    duration_seconds += int(secs_part)
        
        # Create reference video entry
        ref_video = ReferenceVideo(
            youtube_video_id=video_id,
            youtube_video_url=request.youtube_video_url,
            title=snippet['title'],
            description=snippet.get('description', ''),
            duration_seconds=duration_seconds,
            owner_channel_id=request.owner_channel_id,
            analyzed=False
        )
        
        await db.reference_videos.insert_one(ref_video.dict())
        
        # Start analysis in background
        background_tasks.add_task(
            analyze_and_store_video_reference,
            ref_video.id,
            snippet['title'],
            snippet.get('description', ''),
            duration_seconds
        )
        
        return {
            "reference_id": ref_video.id,
            "title": snippet['title'],
            "duration_seconds": duration_seconds,
            "duration_formatted": f"{duration_seconds // 3600}h {(duration_seconds % 3600) // 60}m" if duration_seconds > 3600 else f"{duration_seconds // 60}m {duration_seconds % 60}s",
            "status": "analyzing"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding reference video: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def analyze_and_store_channel_reference(reference_id: str, videos: list):
    """Background task to analyze a channel reference"""
    try:
        analysis = await analyze_youtube_channel_content("", videos)
        
        await db.reference_channels.update_one(
            {"id": reference_id},
            {"$set": {
                "analyzed": True,
                "analysis_data": analysis,
                "video_count_analyzed": len(videos)
            }}
        )
        
        logger.info(f"Channel reference {reference_id} analyzed successfully")
        
    except Exception as e:
        logger.error(f"Error analyzing channel reference: {e}")

async def analyze_and_store_video_reference(reference_id: str, title: str, description: str, duration_seconds: int):
    """Background task to analyze a video reference and extract episodes"""
    try:
        analysis = await analyze_youtube_video_content("", title, description)
        
        # For long videos (>30 min), try to extract episode segments
        episodes = []
        if duration_seconds > 1800:  # 30+ minutes
            # Generate episode suggestions based on duration
            avg_episode_length = 180  # 3 minutes
            num_episodes = min(20, duration_seconds // avg_episode_length)
            
            for i in range(int(num_episodes)):
                episodes.append({
                    "episode_number": i + 1,
                    "suggested_theme": analysis.get("themes", ["educational"])[i % len(analysis.get("themes", ["educational"]))],
                    "estimated_start": i * avg_episode_length,
                    "estimated_duration": avg_episode_length
                })
        
        await db.reference_videos.update_one(
            {"id": reference_id},
            {"$set": {
                "analyzed": True,
                "analysis_data": analysis,
                "episodes_extracted": episodes
            }}
        )
        
        logger.info(f"Video reference {reference_id} analyzed successfully with {len(episodes)} episodes")
        
    except Exception as e:
        logger.error(f"Error analyzing video reference: {e}")

@api_router.get("/references/channels/{owner_channel_id}")
async def get_reference_channels(owner_channel_id: str):
    """Get all reference channels for a user's YouTube channel"""
    refs = await db.reference_channels.find({"owner_channel_id": owner_channel_id}).to_list(50)
    
    for ref in refs:
        ref.pop('_id', None)
    
    return {"references": refs}

@api_router.get("/references/videos/{owner_channel_id}")
async def get_reference_videos(owner_channel_id: str):
    """Get all reference videos for a user's YouTube channel"""
    refs = await db.reference_videos.find({"owner_channel_id": owner_channel_id}).to_list(50)
    
    for ref in refs:
        ref.pop('_id', None)
    
    return {"references": refs}

@api_router.get("/references/{reference_id}")
async def get_reference_detail(reference_id: str, reference_type: str = "channel"):
    """Get detailed analysis of a reference"""
    if reference_type == "channel":
        ref = await db.reference_channels.find_one({"id": reference_id})
    else:
        ref = await db.reference_videos.find_one({"id": reference_id})
    
    if not ref:
        raise HTTPException(status_code=404, detail="Reference not found")
    
    ref.pop('_id', None)
    return ref

@api_router.delete("/references/{reference_id}")
async def delete_reference(reference_id: str, reference_type: str = "channel"):
    """Delete a reference"""
    if reference_type == "channel":
        result = await db.reference_channels.delete_one({"id": reference_id})
    else:
        result = await db.reference_videos.delete_one({"id": reference_id})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Reference not found")
    
    return {"message": "Reference deleted"}

@api_router.post("/references/generate")
async def generate_from_reference(request: GenerateFromReferenceRequest, background_tasks: BackgroundTasks):
    """Generate original content inspired by a reference"""
    try:
        # Get reference analysis
        if request.reference_type == "channel":
            ref = await db.reference_channels.find_one({"id": request.reference_id})
            if not ref or not ref.get("analyzed"):
                raise HTTPException(status_code=400, detail="Reference not analyzed yet")
            analysis = ref.get("analysis_data", {})
        else:
            ref = await db.reference_videos.find_one({"id": request.reference_id})
            if not ref or not ref.get("analyzed"):
                raise HTTPException(status_code=400, detail="Reference not analyzed yet")
            analysis = ref.get("analysis_data", {})
        
        # Generate original content
        script_data = await generate_original_content_from_reference(analysis, request.language)
        
        # Create video entry
        video_id = str(uuid.uuid4())
        is_short = request.video_type == "short"
        
        video = Video(
            id=video_id,
            title=script_data.get("title", "Kids Video"),
            description=script_data.get("description", ""),
            script=json.dumps(script_data),
            language=request.language,
            source_type="reference",
            source_url=ref.get("youtube_channel_id") or ref.get("youtube_video_url"),
            channel_id=request.owner_channel_id,
            video_type=request.video_type,
            status="generating",
            tags=script_data.get("tags", [])
        )
        
        await db.videos.insert_one(video.dict())
        
        # Get character info for consistent animation
        characters = script_data.get("generated_characters", [])
        character_info = ""
        if characters:
            char_desc = []
            for c in characters:
                char_desc.append(f"{c['name']}: {c.get('description', 'cute 3D animated character')}")
            character_info = "; ".join(char_desc)
        
        # Start generation
        background_tasks.add_task(
            process_video_generation_with_characters,
            video_id,
            script_data,
            "cocomelon",
            is_short,
            character_info,
            characters
        )
        
        return {
            "video_id": video_id,
            "title": script_data.get("title"),
            "characters": [c["name"] for c in characters],
            "status": "generating"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating from reference: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def process_video_generation_with_characters(video_id: str, script_data: dict, style: str, is_short: bool, character_info: str, characters: list):
    """Background task to generate video with consistent characters"""
    try:
        scenes = []
        
        # Map characters to voices
        character_voices = {}
        for char in characters:
            character_voices[char["name"]] = char.get("voice_id", KID_FRIENDLY_VOICES[0]["id"])
        
        for scene in script_data.get('scenes', []):
            scene_data = {
                "scene_number": scene.get('scene_number', len(scenes) + 1),
                "narration": scene.get('narration', ''),
                "visual_description": scene.get('visual_description', ''),
                "image_base64": None,
                "audio_base64": None,
                "duration_seconds": scene.get('duration_seconds', 5)
            }
            
            # Generate image with character consistency
            logger.info(f"Generating image for scene {scene_data['scene_number']}...")
            image_b64 = await generate_scene_image(
                scene_data['visual_description'], 
                style, 
                is_short,
                character_info
            )
            if image_b64:
                scene_data['image_base64'] = image_b64
            
            # Generate voice - use character voice if mentioned
            voice_id = KID_FRIENDLY_VOICES[0]["id"]
            for char_name, char_voice in character_voices.items():
                if char_name.lower() in scene_data['narration'].lower():
                    voice_id = char_voice
                    break
            
            logger.info(f"Generating voice for scene {scene_data['scene_number']}...")
            audio_b64 = await generate_voice(scene_data['narration'], voice_id)
            if audio_b64:
                scene_data['audio_base64'] = audio_b64
            
            scenes.append(scene_data)
        
        total_duration = sum(s.get('duration_seconds', 5) for s in scenes)
        
        await db.videos.update_one(
            {"id": video_id},
            {
                "$set": {
                    "scenes": scenes,
                    "status": "completed",
                    "duration_seconds": total_duration,
                    "thumbnail_base64": scenes[0]['image_base64'] if scenes else None
                }
            }
        )
        
        logger.info(f"Video {video_id} generation completed!")
        
    except Exception as e:
        logger.error(f"Error in video generation: {e}")
        await db.videos.update_one(
            {"id": video_id},
            {"$set": {"status": "failed"}}
        )

# ---- Voice Endpoints ----

@api_router.get("/voices")
async def list_voices():
    """Get available voices for narration"""
    voices = await get_available_voices()
    return {"voices": voices}

# ---- Short Generation ----

@api_router.get("/shorts/templates")
async def get_short_templates():
    """Get available Short templates"""
    return {"templates": SHORT_TEMPLATES}

@api_router.post("/generate/short")
async def generate_short_video(request: GenerateShortRequest, background_tasks: BackgroundTasks):
    """Generate a viral YouTube Short"""
    try:
        template = next((t for t in SHORT_TEMPLATES if t["type"] == request.template_type), SHORT_TEMPLATES[0])
        theme = request.theme or random.choice(template["themes"])
        
        script_data = await generate_short_script(
            request.template_type,
            theme,
            request.language
        )
        
        video_id = str(uuid.uuid4())
        video = Video(
            id=video_id,
            title=script_data.get("title", "Kids Short"),
            description=script_data.get("description", ""),
            script=json.dumps(script_data),
            language=request.language,
            video_type="short",
            channel_id=request.channel_id,
            status="generating",
            tags=script_data.get("tags", [])
        )
        
        await db.videos.insert_one(video.dict())
        
        background_tasks.add_task(
            process_video_generation,
            video_id,
            script_data,
            "cartoon",
            True
        )
        
        return {
            "video_id": video_id,
            "status": "generating",
            "message": "Short generation started"
        }
        
    except Exception as e:
        logger.error(f"Error generating short: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ---- Video Generation Endpoints ----

@api_router.post("/generate/script")
async def generate_video_script(request: GenerateVideoRequest):
    """Generate a video script from input"""
    script_data = await generate_script(
        request.input_text,
        request.language,
        max_scenes=min(10, request.max_duration_minutes * 3)
    )
    return script_data

@api_router.post("/generate/video")
async def generate_complete_video(request: GenerateVideoRequest, background_tasks: BackgroundTasks):
    """Generate a complete video with images and voice"""
    try:
        logger.info(f"Generating script for: {request.input_text[:50]}...")
        script_data = await generate_script(
            request.input_text,
            request.language,
            max_scenes=min(10, request.max_duration_minutes * 3)
        )
        
        video_id = str(uuid.uuid4())
        video = Video(
            id=video_id,
            title=script_data.get('title', 'Untitled Video'),
            description=script_data.get('description', ''),
            script=json.dumps(script_data),
            language=request.language,
            source_type="manual",
            series_id=request.series_id,
            channel_id=request.channel_id,
            video_type=request.video_type,
            status="generating"
        )
        
        await db.videos.insert_one(video.dict())
        
        background_tasks.add_task(
            process_video_generation,
            video_id,
            script_data,
            request.style,
            request.video_type == "short"
        )
        
        return {
            "video_id": video_id,
            "status": "generating",
            "message": "Video generation started. Check status with GET /api/videos/{video_id}"
        }
        
    except Exception as e:
        logger.error(f"Error generating video: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def process_video_generation(video_id: str, script_data: dict, style: str, is_short: bool = False):
    """Background task to generate video content"""
    try:
        scenes = []
        
        for scene in script_data.get('scenes', []):
            scene_data = {
                "scene_number": scene.get('scene_number', len(scenes) + 1),
                "narration": scene.get('narration', ''),
                "visual_description": scene.get('visual_description', ''),
                "image_base64": None,
                "audio_base64": None,
                "duration_seconds": scene.get('duration_seconds', 5)
            }
            
            logger.info(f"Generating image for scene {scene_data['scene_number']}...")
            image_b64 = await generate_scene_image(scene_data['visual_description'], style, is_short)
            if image_b64:
                scene_data['image_base64'] = image_b64
            
            logger.info(f"Generating voice for scene {scene_data['scene_number']}...")
            audio_b64 = await generate_voice(scene_data['narration'])
            if audio_b64:
                scene_data['audio_base64'] = audio_b64
            
            scenes.append(scene_data)
        
        total_duration = sum(s.get('duration_seconds', 5) for s in scenes)
        
        await db.videos.update_one(
            {"id": video_id},
            {
                "$set": {
                    "scenes": scenes,
                    "status": "completed",
                    "duration_seconds": total_duration,
                    "thumbnail_base64": scenes[0]['image_base64'] if scenes else None
                }
            }
        )
        
        logger.info(f"Video {video_id} generation completed!")
        
    except Exception as e:
        logger.error(f"Error in video generation: {e}")
        await db.videos.update_one(
            {"id": video_id},
            {"$set": {"status": "failed"}}
        )

# ---- Video CRUD ----

@api_router.get("/videos")
async def list_videos(
    series_id: Optional[str] = None,
    status: Optional[str] = None,
    channel_id: Optional[str] = None,
    video_type: Optional[str] = None
):
    """List all videos"""
    query = {}
    if series_id:
        query["series_id"] = series_id
    if status:
        query["status"] = status
    if channel_id:
        query["channel_id"] = channel_id
    if video_type:
        query["video_type"] = video_type
    
    videos = await db.videos.find(query).sort("created_at", -1).to_list(100)
    
    for video in videos:
        video.pop('_id', None)
        for scene in video.get('scenes', []):
            scene.pop('image_base64', None)
            scene.pop('audio_base64', None)
    
    return {"videos": videos}

@api_router.get("/videos/{video_id}")
async def get_video(video_id: str):
    """Get a specific video with all content"""
    video = await db.videos.find_one({"id": video_id})
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    
    video.pop('_id', None)
    return video

@api_router.delete("/videos/{video_id}")
async def delete_video(video_id: str):
    """Delete a video"""
    result = await db.videos.delete_one({"id": video_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Video not found")
    return {"message": "Video deleted"}

@api_router.put("/videos/{video_id}")
async def update_video(video_id: str, title: str = None, language: str = None, channel_id: str = None):
    """Update video details"""
    update_data = {}
    if title:
        update_data["title"] = title
    if language:
        update_data["language"] = language
    if channel_id:
        update_data["channel_id"] = channel_id
    
    if update_data:
        await db.videos.update_one({"id": video_id}, {"$set": update_data})
    
    return {"message": "Video updated"}

# ---- Translation ----

@api_router.post("/videos/{video_id}/translate")
async def translate_video(video_id: str, request: TranslateRequest, background_tasks: BackgroundTasks):
    """Translate a video to another language"""
    video = await db.videos.find_one({"id": video_id})
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    
    new_video_id = str(uuid.uuid4())
    new_video = {
        **video,
        "id": new_video_id,
        "language": request.target_language,
        "status": "generating",
        "created_at": datetime.utcnow()
    }
    new_video.pop('_id', None)
    
    await db.videos.insert_one(new_video)
    
    background_tasks.add_task(
        process_translation,
        new_video_id,
        video.get('scenes', []),
        request.target_language
    )
    
    return {
        "video_id": new_video_id,
        "status": "generating",
        "message": "Translation started"
    }

async def process_translation(video_id: str, scenes: list, target_language: str):
    """Background task to translate video"""
    try:
        translated_scenes = []
        
        for scene in scenes:
            translated_narration = await translate_text(scene.get('narration', ''), target_language)
            
            new_scene = {
                **scene,
                "narration": translated_narration,
                "audio_base64": None
            }
            
            audio_b64 = await generate_voice(translated_narration)
            if audio_b64:
                new_scene['audio_base64'] = audio_b64
            
            translated_scenes.append(new_scene)
        
        await db.videos.update_one(
            {"id": video_id},
            {
                "$set": {
                    "scenes": translated_scenes,
                    "status": "completed"
                }
            }
        )
        
    except Exception as e:
        logger.error(f"Translation error: {e}")
        await db.videos.update_one(
            {"id": video_id},
            {"$set": {"status": "failed"}}
        )

# ---- Series Endpoints ----

@api_router.post("/series")
async def create_series(
    title: str,
    description: str,
    style: str = "cartoon",
    target_age: str = "3-8",
    youtube_channel_url: Optional[str] = None,
    channel_id: Optional[str] = None
):
    """Create a new series"""
    series = Series(
        title=title,
        description=description,
        style=style,
        target_age=target_age,
        youtube_channel_inspiration=youtube_channel_url,
        channel_id=channel_id
    )
    
    await db.series.insert_one(series.dict())
    
    return series.dict()

@api_router.get("/series")
async def list_series(channel_id: Optional[str] = None):
    """List all series"""
    query = {}
    if channel_id:
        query["channel_id"] = channel_id
    
    series_list = await db.series.find(query).sort("created_at", -1).to_list(50)
    for s in series_list:
        s.pop('_id', None)
    return {"series": series_list}

@api_router.get("/series/{series_id}")
async def get_series(series_id: str):
    """Get a specific series with episodes"""
    series = await db.series.find_one({"id": series_id})
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    
    series.pop('_id', None)
    
    episodes = await db.videos.find({"series_id": series_id}).sort("created_at", 1).to_list(100)
    for ep in episodes:
        ep.pop('_id', None)
        for scene in ep.get('scenes', []):
            scene.pop('image_base64', None)
            scene.pop('audio_base64', None)
    
    return {**series, "episodes": episodes}

@api_router.post("/series/{series_id}/generate-episode")
async def generate_series_episode(
    series_id: str,
    episode_theme: str,
    background_tasks: BackgroundTasks
):
    """Generate a new episode for a series"""
    series = await db.series.find_one({"id": series_id})
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    
    episode_count = await db.videos.count_documents({"series_id": series_id})
    
    characters = await db.characters.find({"id": {"$in": series.get('characters', [])}}).to_list(10)
    character_descriptions = "\n".join([f"- {c['name']}: {c['description']}" for c in characters])
    
    prompt = f"""Series: {series['title']}
Description: {series['description']}
Target Age: {series['target_age']}
Episode Number: {episode_count + 1}
Characters:
{character_descriptions if character_descriptions else 'Create new fun characters'}

Episode Theme: {episode_theme}

Create an engaging episode that continues the series style and uses the recurring characters."""

    script_data = await generate_script(prompt, "en", max_scenes=8)
    
    video_id = str(uuid.uuid4())
    video = Video(
        id=video_id,
        title=f"{series['title']} - Episode {episode_count + 1}: {script_data.get('title', episode_theme)}",
        description=script_data.get('description', ''),
        script=json.dumps(script_data),
        language="en",
        source_type="series",
        series_id=series_id,
        channel_id=series.get('channel_id'),
        status="generating"
    )
    
    await db.videos.insert_one(video.dict())
    
    await db.series.update_one(
        {"id": series_id},
        {"$inc": {"total_episodes": 1}}
    )
    
    background_tasks.add_task(
        process_video_generation,
        video_id,
        script_data,
        series.get('style', 'cartoon'),
        False
    )
    
    return {
        "video_id": video_id,
        "episode_number": episode_count + 1,
        "status": "generating"
    }

# ---- Character Endpoints ----

@api_router.post("/characters")
async def create_character(
    name: str,
    description: str,
    appearance: str,
    personality: str,
    series_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    background_tasks: BackgroundTasks = None
):
    """Create a new character"""
    character = Character(
        name=name,
        description=description,
        appearance=appearance,
        personality=personality,
        channel_id=channel_id
    )
    
    image_prompt = f"Character design for children's show: {name}. {appearance}. {personality}. Cute, friendly, colorful cartoon style."
    image_b64 = await generate_scene_image(image_prompt, "cartoon")
    if image_b64:
        character.image_base64 = image_b64
    
    await db.characters.insert_one(character.dict())
    
    if series_id:
        await db.series.update_one(
            {"id": series_id},
            {"$push": {"characters": character.id}}
        )
    
    return character.dict()

@api_router.get("/characters")
async def list_characters(series_id: Optional[str] = None, channel_id: Optional[str] = None):
    """List all characters"""
    query = {}
    if series_id:
        series = await db.series.find_one({"id": series_id})
        if series:
            query = {"id": {"$in": series.get('characters', [])}}
    elif channel_id:
        query = {"channel_id": channel_id}
    
    characters = await db.characters.find(query).to_list(50)
    
    for c in characters:
        c.pop('_id', None)
    
    return {"characters": characters}

# ---- Auto-Publishing Endpoints ----

@api_router.post("/auto-publish/generate/{channel_id}")
async def generate_daily_content(channel_id: str, background_tasks: BackgroundTasks):
    """Generate daily content for a specific channel"""
    result = await auto_generate_content_for_channel(channel_id, background_tasks)
    return {
        "message": "Content generation started",
        "channel_id": channel_id,
        "generated": result
    }

@api_router.post("/auto-publish/process-queue")
async def process_publishing_queue(background_tasks: BackgroundTasks):
    """Process all pending scheduled posts"""
    results = await process_scheduled_posts(background_tasks)
    return {"processed": len(results), "posts": results}

@api_router.get("/auto-publish/queue/{channel_id}")
async def get_publishing_queue(channel_id: str):
    """Get pending scheduled posts for a channel"""
    posts = await db.scheduled_posts.find({
        "channel_id": channel_id,
        "status": {"$in": ["pending", "publishing"]}
    }).sort("scheduled_time", 1).to_list(50)
    
    for post in posts:
        post.pop('_id', None)
        # Convert datetime to ISO string
        if post.get('scheduled_time'):
            post['scheduled_time'] = post['scheduled_time'].isoformat()
        if post.get('created_at'):
            post['created_at'] = post['created_at'].isoformat()
    
    return {"queue": posts}

@api_router.get("/auto-publish/quota/{channel_id}")
async def get_daily_quota(channel_id: str):
    """Get publishing quota for today"""
    quota = await get_or_create_daily_quota(channel_id)
    quota.pop('_id', None)
    return quota

@api_router.post("/auto-generate/daily")
async def trigger_daily_auto_generation(background_tasks: BackgroundTasks):
    """Trigger daily auto-generation for all channels"""
    channels = await db.youtube_channels.find({"auto_publish_enabled": True}).to_list(50)
    
    results = []
    for channel in channels:
        result = await auto_generate_content_for_channel(channel["id"], background_tasks)
        results.append({
            "channel_id": channel["id"],
            "channel_title": channel["channel_title"],
            "generated": result
        })
    
    return {
        "message": "Daily auto-generation triggered",
        "channels_processed": len(results),
        "results": results
    }

@api_router.get("/gallery")
async def get_gallery(channel_id: Optional[str] = None):
    """Get organized gallery with series albums and standalone videos"""
    query = {}
    if channel_id:
        query["channel_id"] = channel_id
    
    series_list = await db.series.find(query).sort("created_at", -1).to_list(50)
    
    albums = []
    for series in series_list:
        series.pop('_id', None)
        episodes = await db.videos.find({"series_id": series['id']}).sort("created_at", 1).to_list(100)
        
        episode_summaries = []
        for ep in episodes:
            episode_summaries.append({
                "id": ep['id'],
                "title": ep['title'],
                "status": ep['status'],
                "video_type": ep.get('video_type', 'normal'),
                "thumbnail_base64": ep.get('thumbnail_base64'),
                "youtube_url": ep.get('youtube_url'),
                "created_at": ep['created_at'].isoformat() if isinstance(ep['created_at'], datetime) else ep['created_at']
            })
        
        albums.append({
            "id": series['id'],
            "title": series['title'],
            "type": "series",
            "episode_count": len(episode_summaries),
            "is_completed": series.get('is_completed', False),
            "episodes": episode_summaries
        })
    
    # Get standalone videos
    standalone_query = {"series_id": None}
    if channel_id:
        standalone_query["channel_id"] = channel_id
    
    standalone_videos = await db.videos.find(standalone_query).sort("created_at", -1).to_list(100)
    
    standalone_summaries = []
    for video in standalone_videos:
        standalone_summaries.append({
            "id": video['id'],
            "title": video['title'],
            "status": video['status'],
            "video_type": video.get('video_type', 'normal'),
            "thumbnail_base64": video.get('thumbnail_base64'),
            "source_type": video.get('source_type', 'manual'),
            "youtube_url": video.get('youtube_url'),
            "scheduled_for": video.get('scheduled_for').isoformat() if video.get('scheduled_for') else None,
            "created_at": video['created_at'].isoformat() if isinstance(video['created_at'], datetime) else video['created_at']
        })
    
    return {
        "albums": albums,
        "standalone_videos": standalone_summaries
    }

# ---- Dashboard Stats ----

@api_router.get("/dashboard/stats")
async def get_dashboard_stats():
    """Get overall dashboard statistics"""
    total_videos = await db.videos.count_documents({})
    total_shorts = await db.videos.count_documents({"video_type": "short"})
    total_series = await db.series.count_documents({})
    total_characters = await db.characters.count_documents({})
    total_channels = await db.youtube_channels.count_documents({})
    
    published_videos = await db.videos.count_documents({"youtube_url": {"$ne": None}})
    pending_posts = await db.scheduled_posts.count_documents({"status": "pending"})
    
    # Today's activity
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    videos_today = await db.videos.count_documents({"created_at": {"$gte": today_start}})
    
    return {
        "total_videos": total_videos,
        "total_shorts": total_shorts,
        "total_series": total_series,
        "total_characters": total_characters,
        "total_channels": total_channels,
        "published_videos": published_videos,
        "pending_posts": pending_posts,
        "videos_today": videos_today
    }

@api_router.get("/dashboard/channel/{channel_id}")
async def get_channel_dashboard(channel_id: str):
    """Get dashboard stats for a specific channel"""
    channel = await db.youtube_channels.find_one({"id": channel_id})
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    
    total_videos = await db.videos.count_documents({"channel_id": channel_id})
    shorts = await db.videos.count_documents({"channel_id": channel_id, "video_type": "short"})
    normal = await db.videos.count_documents({"channel_id": channel_id, "video_type": "normal"})
    published = await db.videos.count_documents({"channel_id": channel_id, "youtube_url": {"$ne": None}})
    
    quota = await get_or_create_daily_quota(channel_id)
    
    pending_posts = await db.scheduled_posts.find({
        "channel_id": channel_id,
        "status": "pending"
    }).sort("scheduled_time", 1).to_list(10)
    
    for post in pending_posts:
        post.pop('_id', None)
        if post.get('scheduled_time'):
            post['scheduled_time'] = post['scheduled_time'].isoformat()
    
    return {
        "channel_id": channel_id,
        "channel_title": channel.get("channel_title"),
        "total_videos": total_videos,
        "shorts": shorts,
        "normal_videos": normal,
        "published": published,
        "quota": {
            "shorts_today": quota["shorts_published"],
            "shorts_target": quota["shorts_target"],
            "videos_today": quota["videos_published"],
            "videos_target": quota["videos_target"]
        },
        "upcoming_posts": pending_posts,
        "settings": {
            "auto_publish_enabled": channel.get("auto_publish_enabled", True),
            "shorts_per_day": channel.get("shorts_per_day", 4),
            "videos_per_day": channel.get("videos_per_day", 1),
            "target_language": channel.get("target_language", "en")
        }
    }

# ---- YouTube OAuth & Publishing ----

@api_router.get("/youtube/oauth/url")
async def get_youtube_oauth_url():
    """Get YouTube OAuth authorization URL"""
    scopes = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/youtube"
    ]
    
    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={YOUTUBE_CLIENT_ID}&"
        f"redirect_uri={YOUTUBE_REDIRECT_URI}&"
        f"response_type=code&"
        f"scope={' '.join(scopes)}&"
        f"access_type=offline&"
        f"prompt=consent"
    )
    
    return {"auth_url": auth_url}

@api_router.get("/youtube/callback")
async def youtube_oauth_callback(code: str, background_tasks: BackgroundTasks):
    """Handle YouTube OAuth callback"""
    try:
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": YOUTUBE_CLIENT_ID,
                    "client_secret": YOUTUBE_CLIENT_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": YOUTUBE_REDIRECT_URI
                }
            )
            
            if token_response.status_code != 200:
                logger.error(f"Token exchange failed: {token_response.text}")
                raise HTTPException(status_code=400, detail="Failed to exchange token")
            
            tokens = token_response.json()
            access_token = tokens.get("access_token")
            refresh_token = tokens.get("refresh_token")
            expires_in = tokens.get("expires_in", 3600)
            
            channel_response = await client.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if channel_response.status_code != 200:
                raise HTTPException(status_code=400, detail="Failed to get channel info")
            
            channel_data = channel_response.json()
            if not channel_data.get("items"):
                raise HTTPException(status_code=400, detail="No YouTube channel found")
            
            channel = channel_data["items"][0]
            channel_id = channel["id"]
            channel_title = channel["snippet"]["title"]
            channel_thumbnail = channel["snippet"]["thumbnails"]["default"]["url"]
            
            existing = await db.youtube_channels.find_one({"channel_id": channel_id})
            
            youtube_channel = YouTubeChannel(
                id=existing["id"] if existing else str(uuid.uuid4()),
                channel_id=channel_id,
                channel_title=channel_title,
                channel_thumbnail=channel_thumbnail,
                access_token=access_token,
                refresh_token=refresh_token,
                token_expiry=datetime.utcnow() + timedelta(seconds=expires_in)
            )
            
            if existing:
                await db.youtube_channels.update_one(
                    {"channel_id": channel_id},
                    {"$set": youtube_channel.dict()}
                )
            else:
                await db.youtube_channels.insert_one(youtube_channel.dict())
                # Auto-generate initial content for new channel
                background_tasks.add_task(
                    auto_generate_content_for_channel,
                    youtube_channel.id,
                    background_tasks
                )
            
            from fastapi.responses import HTMLResponse
            return HTMLResponse(content=f"""
                <html>
                <body style="font-family: Arial; text-align: center; padding: 50px; background: #0a0a1a; color: white;">
                    <h2 style="color: #4CAF50;">YouTube Connected Successfully!</h2>
                    <p>Channel: <strong>{channel_title}</strong></p>
                    <p style="color: #888;">Auto-publishing has been enabled for this channel.</p>
                    <p style="color: #888;">You can close this window and return to the app.</p>
                    <script>
                        if (window.opener) {{
                            window.opener.postMessage({{type: 'youtube_connected', channel: '{channel_title}'}}, '*');
                            setTimeout(() => window.close(), 2000);
                        }}
                    </script>
                </body>
                </html>
            """, status_code=200)
            
    except Exception as e:
        logger.error(f"YouTube OAuth error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/youtube/channels")
async def list_youtube_channels():
    """List all connected YouTube channels"""
    channels = await db.youtube_channels.find().to_list(50)
    
    result = []
    for channel in channels:
        result.append({
            "id": channel["id"],
            "channel_id": channel["channel_id"],
            "channel_title": channel["channel_title"],
            "channel_thumbnail": channel.get("channel_thumbnail"),
            "auto_publish_enabled": channel.get("auto_publish_enabled", True),
            "shorts_per_day": channel.get("shorts_per_day", 4),
            "videos_per_day": channel.get("videos_per_day", 1),
            "target_language": channel.get("target_language", "en"),
            "animation_style": channel.get("animation_style", "3d_cocomelon"),
            "connected_at": channel["created_at"].isoformat() if isinstance(channel["created_at"], datetime) else channel["created_at"]
        })
    
    return {"channels": result}

@api_router.get("/youtube/channels/{channel_id}")
async def get_youtube_channel(channel_id: str):
    """Get a specific YouTube channel details"""
    channel = await db.youtube_channels.find_one({"id": channel_id})
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    
    channel.pop('_id', None)
    channel.pop('access_token', None)
    channel.pop('refresh_token', None)
    
    return channel

@api_router.put("/youtube/channels/{channel_id}/settings")
async def update_channel_settings(channel_id: str, settings: ChannelSettingsUpdate):
    """Update channel auto-publishing settings"""
    update_data = {}
    if settings.auto_publish_enabled is not None:
        update_data["auto_publish_enabled"] = settings.auto_publish_enabled
    if settings.shorts_per_day is not None:
        update_data["shorts_per_day"] = settings.shorts_per_day
    if settings.videos_per_day is not None:
        update_data["videos_per_day"] = settings.videos_per_day
    if settings.target_language is not None:
        update_data["target_language"] = settings.target_language
    if settings.music_style is not None:
        update_data["music_style"] = settings.music_style
    if settings.animation_style is not None:
        update_data["animation_style"] = settings.animation_style
    
    if update_data:
        await db.youtube_channels.update_one(
            {"id": channel_id},
            {"$set": update_data}
        )
    
    return {"message": "Channel settings updated"}

@api_router.delete("/youtube/channels/{channel_id}")
async def disconnect_youtube_channel(channel_id: str):
    """Disconnect a YouTube channel"""
    result = await db.youtube_channels.delete_one({"id": channel_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Channel not found")
    return {"message": "Channel disconnected"}

async def refresh_youtube_token(channel_id: str):
    """Refresh YouTube access token"""
    channel = await db.youtube_channels.find_one({"id": channel_id})
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": YOUTUBE_CLIENT_ID,
                "client_secret": YOUTUBE_CLIENT_SECRET,
                "refresh_token": channel["refresh_token"],
                "grant_type": "refresh_token"
            }
        )
        
        if token_response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to refresh token")
        
        tokens = token_response.json()
        
        await db.youtube_channels.update_one(
            {"id": channel_id},
            {"$set": {
                "access_token": tokens["access_token"],
                "token_expiry": datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 3600))
            }}
        )
        
        return tokens["access_token"]

@api_router.post("/youtube/publish")
async def publish_to_youtube(request: PublishVideoRequest, background_tasks: BackgroundTasks):
    """Publish a video to YouTube"""
    video = await db.videos.find_one({"id": request.video_id})
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    
    if video["status"] != "completed":
        raise HTTPException(status_code=400, detail="Video is not ready for publishing")
    
    channel = await db.youtube_channels.find_one({"id": request.channel_id})
    if not channel:
        raise HTTPException(status_code=404, detail="YouTube channel not found")
    
    if datetime.utcnow() >= channel["token_expiry"]:
        access_token = await refresh_youtube_token(request.channel_id)
    else:
        access_token = channel["access_token"]
    
    background_tasks.add_task(
        process_youtube_publish,
        video,
        channel,
        access_token,
        request.title,
        request.description,
        request.tags,
        request.privacy_status,
        None
    )
    
    return {
        "message": "Publishing started",
        "video_id": request.video_id,
        "channel": channel["channel_title"]
    }

async def process_youtube_publish(video: dict, channel: dict, access_token: str, 
                                   title: str, description: str, tags: list, 
                                   privacy_status: str, scheduled_post_id: str = None):
    """Background task to create video file and upload to YouTube"""
    try:
        logger.info(f"Starting YouTube publish for video {video['id']}")
        
        video_path = await create_video_file(video)
        
        if not video_path:
            logger.error("Failed to create video file")
            if scheduled_post_id:
                await db.scheduled_posts.update_one(
                    {"id": scheduled_post_id},
                    {"$set": {"status": "failed", "error_message": "Failed to create video file"}}
                )
            return
        
        is_short = video.get('video_type') == 'short'
        
        # Add #Shorts to title if it's a Short
        if is_short and "#Shorts" not in title:
            title = f"{title} #Shorts"
        
        async with httpx.AsyncClient(timeout=300) as client:
            metadata = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": tags,
                    "categoryId": "1"
                },
                "status": {
                    "privacyStatus": privacy_status,
                    "selfDeclaredMadeForKids": True
                }
            }
            
            init_response = await client.post(
                "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                },
                json=metadata
            )
            
            if init_response.status_code not in [200, 201]:
                logger.error(f"YouTube upload init failed: {init_response.text}")
                if scheduled_post_id:
                    await db.scheduled_posts.update_one(
                        {"id": scheduled_post_id},
                        {"$set": {"status": "failed", "error_message": init_response.text}}
                    )
                return
            
            upload_url = init_response.headers.get("Location")
            
            with open(video_path, "rb") as video_file:
                video_data = video_file.read()
            
            upload_response = await client.put(
                upload_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "video/mp4"
                },
                content=video_data
            )
            
            if upload_response.status_code in [200, 201]:
                youtube_video_id = upload_response.json().get("id")
                logger.info(f"Video published to YouTube: {youtube_video_id}")
                
                await db.videos.update_one(
                    {"id": video["id"]},
                    {"$set": {
                        "youtube_video_id": youtube_video_id,
                        "youtube_url": f"https://youtube.com/watch?v={youtube_video_id}",
                        "published_at": datetime.utcnow()
                    }}
                )
                
                # Update quota
                today = datetime.utcnow().strftime("%Y-%m-%d")
                video_type_field = "shorts_published" if video.get('video_type') == 'short' else "videos_published"
                await db.publishing_quotas.update_one(
                    {"channel_id": channel["id"], "date": today},
                    {"$inc": {video_type_field: 1}},
                    upsert=True
                )
                
                if scheduled_post_id:
                    await db.scheduled_posts.update_one(
                        {"id": scheduled_post_id},
                        {"$set": {"status": "published"}}
                    )
            else:
                logger.error(f"YouTube upload failed: {upload_response.text}")
                if scheduled_post_id:
                    await db.scheduled_posts.update_one(
                        {"id": scheduled_post_id},
                        {"$set": {"status": "failed", "error_message": upload_response.text}}
                    )
        
        if os.path.exists(video_path):
            os.remove(video_path)
            
    except Exception as e:
        logger.error(f"YouTube publish error: {e}")
        if scheduled_post_id:
            await db.scheduled_posts.update_one(
                {"id": scheduled_post_id},
                {"$set": {"status": "failed", "error_message": str(e)}}
            )

async def create_video_file(video: dict) -> Optional[str]:
    """Create an MP4 video file from scenes (images + audio)"""
    try:
        from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips
        import tempfile
        
        clips = []
        temp_files = []
        
        is_short = video.get('video_type') == 'short'
        
        for i, scene in enumerate(video.get("scenes", [])):
            if not scene.get("image_base64"):
                continue
            
            img_data = base64.b64decode(scene["image_base64"])
            img_path = f"/tmp/scene_{video['id']}_{i}.png"
            with open(img_path, "wb") as f:
                f.write(img_data)
            temp_files.append(img_path)
            
            duration = scene.get('duration_seconds', 5)
            
            if scene.get("audio_base64"):
                audio_data = base64.b64decode(scene["audio_base64"])
                audio_path = f"/tmp/audio_{video['id']}_{i}.mp3"
                with open(audio_path, "wb") as f:
                    f.write(audio_data)
                temp_files.append(audio_path)
                
                audio_clip = AudioFileClip(audio_path)
                duration = audio_clip.duration + 0.5
                
                img_clip = ImageClip(img_path, duration=duration)
                img_clip = img_clip.set_audio(audio_clip)
            else:
                img_clip = ImageClip(img_path, duration=duration)
            
            # Resize for Shorts (9:16) or normal (16:9)
            if is_short:
                img_clip = img_clip.resize(height=1920)
            else:
                img_clip = img_clip.resize(width=1920)
            
            clips.append(img_clip)
        
        if not clips:
            return None
        
        final_clip = concatenate_videoclips(clips, method="compose")
        
        output_path = str(GENERATED_DIR / "videos" / f"{video['id']}.mp4")
        final_clip.write_videofile(
            output_path,
            fps=24,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=f"/tmp/temp_audio_{video['id']}.m4a",
            remove_temp=True,
            verbose=False,
            logger=None
        )
        
        for temp_file in temp_files:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        
        return output_path
        
    except Exception as e:
        logger.error(f"Error creating video file: {e}")
        return None

# Import additional modules for YouTube OAuth
from fastapi.responses import HTMLResponse

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Background Auto-Scheduler ----
scheduler_task = None

async def auto_scheduler_loop():
    """Background loop that automatically generates content and publishes scheduled posts"""
    logger.info("🚀 Auto-scheduler started - will check every 10 minutes")
    
    while True:
        try:
            # 1. Process scheduled posts that are due
            now = datetime.utcnow()
            pending_posts = await db.scheduled_posts.find({
                "status": "pending",
                "scheduled_time": {"$lte": now}
            }).to_list(100)
            
            if pending_posts:
                logger.info(f"📤 Processing {len(pending_posts)} scheduled posts...")
                for post in pending_posts:
                    try:
                        video = await db.videos.find_one({"id": post["video_id"]})
                        if video and video.get("status") == "completed":
                            channel = await db.youtube_channels.find_one({"id": post["channel_id"]})
                            if channel and channel.get("access_token"):
                                # Attempt to publish
                                success = await publish_video_to_youtube(
                                    video_id=post["video_id"],
                                    channel_id=post["channel_id"]
                                )
                                if success:
                                    await db.scheduled_posts.update_one(
                                        {"_id": post["_id"]},
                                        {"$set": {"status": "published"}}
                                    )
                                    logger.info(f"✅ Published video {post['video_id']} to channel {post['channel_id']}")
                                else:
                                    logger.warning(f"⚠️ Failed to publish video {post['video_id']}")
                            else:
                                logger.warning(f"⚠️ Channel {post.get('channel_id')} not found or no access token")
                        elif video and video.get("status") == "generating":
                            logger.info(f"⏳ Video {post['video_id']} still generating, will retry later")
                        else:
                            # Video failed or doesn't exist, mark post as failed
                            await db.scheduled_posts.update_one(
                                {"_id": post["_id"]},
                                {"$set": {"status": "failed"}}
                            )
                    except Exception as e:
                        logger.error(f"Error processing post {post.get('video_id')}: {e}")
            
            # 2. Auto-generate content for channels that need it
            channels = await db.youtube_channels.find({"auto_publish_enabled": True}).to_list(50)
            
            for channel in channels:
                try:
                    channel_id = channel["id"]
                    quota = await get_or_create_daily_quota(channel_id)
                    
                    shorts_needed = quota["shorts_target"] - quota["shorts_published"] - quota.get("shorts_generating", 0)
                    videos_needed = quota["videos_target"] - quota["videos_published"] - quota.get("videos_generating", 0)
                    
                    # Check if there are already enough pending/generating videos
                    generating_count = await db.videos.count_documents({
                        "channel_id": channel_id,
                        "status": {"$in": ["generating", "pending"]},
                        "created_at": {"$gte": datetime.utcnow().replace(hour=0, minute=0, second=0)}
                    })
                    
                    if generating_count >= (quota["shorts_target"] + quota["videos_target"]):
                        continue  # Already have enough content being generated
                    
                    if shorts_needed > 0 or videos_needed > 0:
                        logger.info(f"🎬 Auto-generating for channel {channel.get('channel_title')}: {shorts_needed} shorts, {videos_needed} videos needed")
                        
                        # Generate shorts
                        for _ in range(max(0, shorts_needed)):
                            try:
                                template = random.choice(SHORT_TEMPLATES)
                                theme = random.choice(template["themes"])
                                script_data = await generate_short_script(
                                    template["type"],
                                    theme,
                                    channel.get("target_language", "en")
                                )
                                video_id = str(uuid.uuid4())
                                video = Video(
                                    id=video_id,
                                    title=script_data.get("title", "Kids Short"),
                                    description=script_data.get("description", ""),
                                    script=json.dumps(script_data),
                                    language=channel.get("target_language", "en"),
                                    video_type="short",
                                    channel_id=channel_id,
                                    status="generating",
                                    tags=script_data.get("tags", [])
                                )
                                await db.videos.insert_one(video.dict())
                                asyncio.create_task(process_video_generation(video_id, script_data, "cartoon", True))
                                await schedule_video_for_channel(video_id, channel_id, "short")
                                logger.info(f"⚡ Generated short: {script_data.get('title')}")
                            except Exception as e:
                                logger.error(f"Error generating short: {e}")
                        
                        # Generate videos
                        for _ in range(max(0, videos_needed)):
                            try:
                                themes = ["learning colors", "counting numbers", "animal sounds", "alphabet fun", "shapes adventure", "nursery rhymes", "baby songs"]
                                prompt = f"Create an educational video about: {random.choice(themes)}"
                                script_data = await generate_script(
                                    prompt,
                                    channel.get("target_language", "en"),
                                    max_scenes=6
                                )
                                video_id = str(uuid.uuid4())
                                video = Video(
                                    id=video_id,
                                    title=script_data.get("title", "Kids Video"),
                                    description=script_data.get("description", ""),
                                    script=json.dumps(script_data),
                                    language=channel.get("target_language", "en"),
                                    video_type="normal",
                                    channel_id=channel_id,
                                    status="generating"
                                )
                                await db.videos.insert_one(video.dict())
                                asyncio.create_task(process_video_generation(video_id, script_data, "cartoon", False))
                                await schedule_video_for_channel(video_id, channel_id, "normal")
                                logger.info(f"🎥 Generated video: {script_data.get('title')}")
                            except Exception as e:
                                logger.error(f"Error generating video: {e}")
                
                except Exception as e:
                    logger.error(f"Error processing channel {channel.get('channel_title')}: {e}")
            
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
        
        # Wait 10 minutes before next check
        await asyncio.sleep(600)

@app.on_event("startup")
async def start_scheduler():
    """Start the background auto-scheduler on app startup"""
    global scheduler_task
    scheduler_task = asyncio.create_task(auto_scheduler_loop())
    logger.info("✅ Background auto-scheduler initialized")

@app.on_event("shutdown")
async def shutdown_db_client():
    global scheduler_task
    if scheduler_task:
        scheduler_task.cancel()
    client.close()
