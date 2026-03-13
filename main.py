import os
import io
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from apify_client import ApifyClient

# ----------------------------------------------------
# Configuration
# ----------------------------------------------------
# The user's Apify API Token. By default we search for it in env,
# but it can be hardcoded here for local testing.
APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN", "YOUR_APIFY_API_TOKEN_HERE")

# The specific Apify actor provided by the user
APIFY_ACTOR_ID = "shu8hvrXbJbY3Eb9W"

app = FastAPI(title="Influencer Discovery API")

# Enable CORS for the frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

apify_client = ApifyClient(APIFY_API_TOKEN)


# ----------------------------------------------------
# Models
# ----------------------------------------------------
class SearchRequest(BaseModel):
    usernames: List[str]

class InfluencerProfile(BaseModel):
    username: str
    full_name: Optional[str] = None
    biography: Optional[str] = None
    followers_count: int = 0
    following_count: int = 0
    posts_count: int = 0
    profile_pic_url: Optional[str] = None
    is_verified: bool = False
    average_likes: int = 0
    average_comments: int = 0
    engagement_rate: float = 0.0
    latest_posts_urls: List[str] = []

class ExportRequest(BaseModel):
    profiles: List[InfluencerProfile]


# ----------------------------------------------------
# API Endpoints
# ----------------------------------------------------
@app.post("/api/search", response_model=List[InfluencerProfile])
async def search_influencers(request: SearchRequest):
    """
    Given a list of Instagram usernames, fetches their latest posts using Apify.
    Calculates average likes, comments, and engagement rate based on those posts.
    """
    if not APIFY_API_TOKEN or APIFY_API_TOKEN == "YOUR_APIFY_API_TOKEN_HERE":
        raise HTTPException(status_code=500, detail="Apify API Token is missing. Please set it in backend/main.py")

    if not request.usernames:
        raise HTTPException(status_code=400, detail="Please provide at least one username.")

    try:
        results = []
        for username in request.usernames:
            # Prepare the Actor input exactly as the user specified, swapping out the URL
            run_input = {
                "directUrls": [f"https://www.instagram.com/{username}/"],
                "resultsType": "posts",
                "resultsLimit": 30, # Limit to 30 posts to calculate recent engagement
                "onlyPostsNewerThan": None,
                "search": None,
                "searchType": "hashtag",
                "searchLimit": 1,
                "addParentData": False,
            }

            # Run the Actor and wait for it to finish
            print(f"Triggering Apify task for: {username}")
            run = apify_client.actor(APIFY_ACTOR_ID).call(run_input=run_input)
            
            # Since this actor returns a list of *posts*, we need to aggregate them
            posts = []
            for item in apify_client.dataset(run["defaultDatasetId"]).iterate_items():
                posts.append(item)

            if not posts:
                 continue # Skip if nothing was found

            # The actor returns post objects. We'll extract owner info from the first post
            first_post = posts[0]
            owner = first_post.get("ownerProfile", first_post.get("owner", {}))
            
            followers = first_post.get("ownerProfile", {}).get("followersCount", 0)
            
            total_likes = 0
            total_comments = 0
            post_count_for_avg = len(posts)
            post_urls = []

            for post in posts:
                total_likes += post.get("likesCount", 0)
                total_comments += post.get("commentsCount", 0)
                if post.get("url"):
                    post_urls.append(post.get("url"))
            
            avg_likes = (total_likes // post_count_for_avg) if post_count_for_avg > 0 else 0
            avg_comments = (total_comments // post_count_for_avg) if post_count_for_avg > 0 else 0
            
            engagement_rate = 0.0
            if followers > 0:
                # Engagement rate formula = (Avg Likes + Avg Comments) / Followers * 100
                engagement_rate = ((avg_likes + avg_comments) / followers) * 100
                engagement_rate = round(engagement_rate, 2)

            profile = InfluencerProfile(
                username=username,
                full_name=first_post.get("ownerFullName", username),
                biography="Biography not provided by this scraper type.",
                followers_count=followers,
                following_count=0, # Not provided by this scraper type
                posts_count=first_post.get("ownerProfile", {}).get("postsCount", 0),
                profile_pic_url=first_post.get("ownerProfile", {}).get("profilePicUrlHD") or first_post.get("ownerProfile", {}).get("profilePicUrl"),
                is_verified=first_post.get("ownerProfile", {}).get("isVerified", False),
                average_likes=avg_likes,
                average_comments=avg_comments,
                engagement_rate=engagement_rate,
                latest_posts_urls=post_urls[:3] # keep only top 3
            )
            results.append(profile)

        return results

    except Exception as e:
        print(f"Error calling Apify: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch data from Apify via Actor {APIFY_ACTOR_ID}. Ensure your API key is correct. Error: {str(e)}")

@app.post("/api/export")
async def export_to_csv(request: ExportRequest):
    """
    Takes a list of influencer profiles and returns a downloadable CSV file.
    """
    if not request.profiles:
        raise HTTPException(status_code=400, detail="No profiles provided for export.")

    # Convert Pydantic models to dicts
    data = [profile.model_dump() for profile in request.profiles]
    
    # Format the data for CSV
    formatted_data = []
    for d in data:
        formatted_data.append({
            "Username": d["username"],
            "Full Name": d["full_name"],
            "Followers": d["followers_count"],
            "Engagement Rate (%)": d["engagement_rate"],
            "Average Likes": d["average_likes"],
            "Average Comments": d["average_comments"],
            "Verified": d["is_verified"],
            "Biography": d["biography"],
            "Profile URL": f"https://instagram.com/{d['username']}"
        })

    df = pd.DataFrame(formatted_data)
    
    # Create in-memory string buffer for CSV
    stream = io.StringIO()
    df.to_csv(stream, index=False)
    
    # Create bytes buffer for streaming response
    response_bytes = stream.getvalue().encode('utf-8')
    bytes_stream = io.BytesIO(response_bytes)

    headers = {
        'Content-Disposition': 'attachment; filename="influencers_export.csv"'
    }

    return StreamingResponse(bytes_stream, media_type="text/csv", headers=headers)

if __name__ == "__main__":
    import uvicorn
    # Make sure to run the server from backend/ using `python main.py` or `uvicorn main:app --reload`
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
