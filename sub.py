from flask import Flask, jsonify
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

@app.route('/')
def home():
    return render_template("sub.html")

# Function to scrape AI News
def scrape_ai_news():
    url = "https://news.google.com/search?q=artificial%20intelligence&hl=en-US&gl=US&ceid=US:en"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, "html.parser")
    
    articles = []
    
    for item in soup.find_all("article")[:5]:  # Get top 5 AI news
        title_tag = item.find("h3")
        if title_tag and title_tag.a:
            title = title_tag.text
            link = "https://news.google.com" + title_tag.a["href"][1:]  # Correct relative link
            articles.append({"title": title, "link": link})

    return articles

@app.route('/get_ai_news', methods=['GET'])
def get_ai_news():
    news = scrape_ai_news()
    return jsonify(news)

if __name__ == '__main__':
    app.run(debug=True)
