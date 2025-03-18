from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import google.generativeai as genai
import mysql.connector
from flask_mail import Mail, Message
from urllib.parse import quote, unquote
import requests
import base64
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")  # Load secret key from .env

# Configure Gemini API Key
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Load Gemini Model
model = genai.GenerativeModel("gemini-2.0-flash")

# Load App URL
APP_URL = os.getenv("APP_URL")

# MySQL Database Configuration
db_config = {
    "host": os.getenv("DB_HOST"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME")
}

# Email Configuration
app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT"))  # Convert to int
app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS") == "True"  # Convert to boolean
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER")

mail = Mail(app)

def format_table(response_text):
    """Converts AI response text with '|' into an HTML table, extracting the table part if necessary."""
    lines = response_text.split("\n")
    table_lines = []
    in_table = False

    for line in lines:
        if line.strip().startswith("|"):
            in_table = True
            table_lines.append(line.strip())
        elif in_table and not line.strip().startswith("|"):
            break  # Stop collecting lines if we exit the table

    if not table_lines:
        return f"<p>{response_text}</p>"  # Return as plain text if no table detected

    table_html = """
    <style>
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th, td { border: 1px solid black; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
    </style>
    <table>
    """

    for index, line in enumerate(table_lines):
        cells = [cell.strip() for cell in line.split("|") if cell.strip()]
        tag = "th" if index == 0 else "td"
        table_html += "<tr>" + "".join(f"<{tag}>{cell}</{tag}>" for cell in cells) + "</tr>"

    table_html += "</table>"
    return table_html

@app.route("/")
def home():
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor(dictionary=True)
    
    # Load all tools at once instead of pagination
    cursor.execute("""
        SELECT tool_name, category, description, price, website, 
               CASE WHEN image_url IS NOT NULL THEN image_url ELSE NULL END as image_url
        FROM ai_tools 
        ORDER BY tool_name
    """)
    tools = cursor.fetchall()
    
    # Get categories in order - first from categories table, then any additional ones from ai_tools
    cursor.execute("""
        SELECT category_name FROM categories ORDER BY id
    """)
    categories = [row['category_name'] for row in cursor.fetchall()]
    
    # Then get any categories from ai_tools that aren't already in the list
    cursor.execute("""
        SELECT DISTINCT category FROM ai_tools 
        WHERE category IS NOT NULL AND category != '' 
        AND category NOT IN (SELECT category_name FROM categories)
        ORDER BY category
    """)
    additional_categories = [row['category'] for row in cursor.fetchall()]
    
    # Combine the lists - categories table first, then additional ones
    categories.extend(additional_categories)
    
    cursor.execute("SELECT DISTINCT price FROM ai_tools WHERE price IS NOT NULL AND price != '' ORDER BY price")
    prices = [row['price'] for row in cursor.fetchall()]
    
    conn.close()
    
    return render_template("home.html", tools=tools, categories=categories, prices=prices)

# Remove the load_more_tools route since we're loading all tools at once
# @app.route("/load_more_tools")
# def load_more_tools():
#     ...

@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    tool1 = data.get("tool1")
    tool2 = data.get("tool2")
    user_message = data.get("message")
    context_type = data.get("context")  # Get context type if provided
    
    if not user_message and not (tool1 and tool2):
        return jsonify({"error": "No message or tools provided"})

    # Detect if the user is asking for a comparison
    comparison_keywords = ["difference between", " vs ", "variation of", "compare", "distinction", "contrast"]
    is_comparison = any(keyword in user_message.lower() for keyword in comparison_keywords) if user_message else False

    # Detect if the user is asking about specific tool categories
    category_keywords = {
        "coding": ["coding", "programming", "developer", "code", "chatbot for coding"],
        "video_editing": ["video editing", "video creator", "video production", "video maker"],
        "image_generation": ["image generation", "image creator", "ai art", "text to image"],
        "audio": ["audio generation", "text to speech", "voice", "speech to text"],
        "writing": ["writing", "content creation", "copywriting", "blog", "article"],
        "productivity": ["productivity", "workflow", "automation", "time management"]
    }
    
    detected_category = None
    for category, keywords in category_keywords.items():
        if any(keyword in user_message.lower() for keyword in keywords):
            detected_category = category
            break
    
    # If context type is provided, use that instead
    if context_type in category_keywords.keys():
        detected_category = context_type
        
    # Handle specific "which is better" questions
    better_keywords = ["which is better", "which one is better", "what is the best", "which ai is better"]
    is_better_question = any(keyword in user_message.lower() for keyword in better_keywords)
    
    if is_better_question and detected_category:
        category_name = detected_category.replace("_", " ")
        
        # Special handling for coding chatbot questions
        if detected_category == "coding" and "chatbot" in user_message.lower():
            prompt = f"""
            The user asked: "{user_message}"
            
            Provide a specific answer about which AI chatbot is best for coding. Your response must:
            
            1. Name specific AI tools (like GitHub Copilot, ChatGPT, Claude, DeepSeek Coder, etc.)
            2. Explain why each is good for coding (with specific features)
            3. Clearly state which one is the best overall and why
            4. Mention which is best for beginners vs. advanced developers
            5. Include pricing information
            
            Format your response with clear headings and a definitive conclusion.
            Do not be vague - name specific tools and make a clear recommendation.
            """
            
            response = model.generate_content(prompt)
            ai_response = response.text.strip()
            
            # Add to chat history
            if "chat_history" not in session:
                session["chat_history"] = []
            
            chat_history = session["chat_history"]
            chat_history.append({"role": "user", "message": user_message})
            chat_history.append({"role": "ai", "message": ai_response})
            session["chat_history"] = chat_history[-6:]
            
            return jsonify({"response": ai_response, "type": "specific_recommendation"})
    
    # Handle category-specific tool recommendations
    if detected_category and "best" in user_message.lower():
        category_name = detected_category.replace("_", " ")
        
        prompt = f"""
        The user asked: "{user_message}"
        
        Provide a specific, detailed comparison of the best AI tools for {category_name}. Focus on:
        
        1. Identify the top 3-5 AI tools specifically for {category_name}
        2. For each tool, provide:
           - Key features and capabilities
           - Unique selling points
           - Pricing information
           - User experience and learning curve
           - Integration with other tools
           - Limitations or drawbacks
        3. Clearly state which tool is best for different scenarios (e.g., beginners, professionals, specific use cases)
        4. Be very specific with your recommendations
        
        Format your response as a clear, structured comparison with a definitive conclusion about which tools are best for {category_name}.
        If appropriate, include a comparison table using the '|' format.
        """
        
        response = model.generate_content(prompt)
        ai_response = response.text.strip()
        
        # Format as table if it contains '|'
        if "|" in ai_response:
            ai_response = format_table(ai_response)
        
        # Add to chat history
        if "chat_history" not in session:
            session["chat_history"] = []
        
        chat_history = session["chat_history"]
        chat_history.append({"role": "user", "message": user_message})
        chat_history.append({"role": "ai", "message": ai_response})
        session["chat_history"] = chat_history[-6:]
        
        return jsonify({"response": ai_response, "type": "category_comparison"})

    # Handle explicit comparison requests (e.g., via compareTools) or detected comparisons
    if tool1 and tool2 or is_comparison:
        if tool1 and tool2:
            prompt = f"""
            Compare these AI tools: {tool1} and {tool2}.
            Provide a structured comparison in the following format:

            | Feature      | {tool1} | {tool2} |
            |-------------|---------|---------|
            | Description | Brief overview of {tool1} | Brief overview of {tool2} |
            | Key Features | List main features of {tool1} | List main features of {tool2} |
            | Use Cases   | Common applications of {tool1} | Common applications of {tool2} |
            | Pricing     | Free or Paid details | Free or Paid details |
            | Limitations | Weaknesses or restrictions | Weaknesses or restrictions |
            | Best For    | Ideal users or industries | Ideal users or industries |

            Ensure the response is in plain text table format with '|' separators, no markdown.
            """
        else:
            # For typed comparison requests without explicit tools, let AI suggest tools
            prompt = f"""
            The user asked: "{user_message}"
            Suggest two relevant AI tools to compare based on the user's query and provide a comparison in this format:

            | Feature      | Tool A | Tool B |
            |-------------|--------|--------|
            | Description | Brief overview of Tool A | Brief overview of Tool B |
            | Key Features | List main features of Tool A | List main features of Tool B |
            | Use Cases   | Common applications of Tool A | Common applications of Tool B |
            | Pricing     | Free or Paid details | Free or Paid details |
            | Limitations | Weaknesses or restrictions | Weaknesses or restrictions |
            | Best For    | Ideal users or industries | Ideal users or industries |

            Ensure the response is in plain text table format with '|' separators, no markdown.
            """

        response = model.generate_content(prompt)
        comparison_result = response.text.strip()
        formatted_response = format_table(comparison_result)
        return jsonify({"response": formatted_response})

    # Handle owner information queries
    owner_keywords = ["owner", "who made", "who created", "who developed", "contact", "email", "developer"]
    if user_message and any(keyword in user_message.lower() for keyword in owner_keywords):
        owner_response = """This website was developed by Dileep Kumar. Contact: dileepkmrc@gmail.com"""
        return jsonify({"response": owner_response, "type": "owner_info"})

    # Handle general chatbot conversation
    if "chat_history" not in session:
        session["chat_history"] = []
    
    chat_history = session["chat_history"]
    chat_history.append({"role": "user", "message": user_message})

    context = "\n".join(f"{msg['role'].capitalize()}: {msg['message']}" for msg in chat_history[-3:])

    prompt = f"""
    You are an AI assistant for AIHubCentral, a platform that helps users discover and compare AI tools.

    Follow this conversation and respond naturally:
    {context}

    If the user requests a comparison of AI tools (e.g., using words like 'compare', 'difference', 'vs'), provide the comparison in a plain text table format with '|' separators, like this:

    | Feature | Tool A | Tool B |
    |--------|--------|--------|
    | Description | ... | ... |
    | Key Features | ... | ... |
    | Use Cases | ... | ... |
    | Pricing | ... | ... |
    | Limitations | ... | ... |
    | Best For | ... | ... |

    Ensure the table is in plain text with '|' separators, no markdown or other formatting.
    For other queries, respond conversationally and helpfully.
    """

    response = model.generate_content(prompt)
    ai_response = response.text.strip()

    # Format as table if it contains '|'
    if "|" in ai_response:
        ai_response = format_table(ai_response)

    chat_history.append({"role": "ai", "message": ai_response})
    session["chat_history"] = chat_history[-6:]

    return jsonify({"response": ai_response})

@app.route("/submit")
def submit():
    return render_template("submit.html")

@app.route("/about")
def about():
    return render_template("about.html")
@app.route("/submit_tool", methods=["POST"])
def submit_tool():
    try:
        tool_name = request.form.get("tool_name")
        category = request.form.get("category")
        description = request.form.get("description")
        price = request.form.get("price")
        website = request.form.get("website")
        email = request.form.get("email")  # Submitter's email
        tags = request.form.get("tags")
        image_url = request.form.get("image_url")
        
        # Handle custom category
        if category == "Other":
            custom_category = request.form.get("custom_category")
            if custom_category:
                category = custom_category.strip()  # Remove any leading/trailing spaces
                
                # Check if this is a new category that needs to be added to the category list
                conn = mysql.connector.connect(**db_config)
                cursor = conn.cursor(dictionary=True)
                
                # Check if category already exists
                cursor.execute("SELECT COUNT(*) as count FROM categories WHERE category_name = %s", (category,))
                result = cursor.fetchone()
                
                # If category doesn't exist, add it to the categories table
                if result and result['count'] == 0:
                    cursor.execute("INSERT INTO categories (category_name) VALUES (%s)", (category,))
                    conn.commit()
                
                conn.close()

        # Create approval and rejection links with all parameters
        approval_link = f"{APP_URL}/approve?tool={quote(tool_name)}&cat={quote(category)}&desc={quote(description)}&price={quote(price)}&web={quote(website)}&email={quote(email)}&tags={quote(tags or '')}&image_url={quote(image_url or '')}"
        rejection_link = f"{APP_URL}/reject?tool={quote(tool_name)}&email={quote(email)}"

        # Professional email body with HTML formatting
        email_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <h2 style="color: #2c3e50;">New AI Tool Submission Request</h2>
            <p>Dear Admin,</p>
            <p>A new AI tool has been submitted for review and approval on <strong>AIHubCentral</strong>. Please review the details below and take appropriate action.</p>
            
            <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                <tr style="background-color: #f2f2f2;">
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Field</th>
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Details</th>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;">Tool Name</td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{tool_name}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;">Category</td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{category}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;">Description</td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{description}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;">Price</td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{price}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;">Website</td>
                    <td style="padding: 10px; border: 1px solid #ddd;"><a href="{website}" style="color: #2980b9;">{website}</a></td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;">Submitted By</td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{email}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;">Tags</td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{tags or 'N/A'}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;">Image URL</td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{image_url or 'N/A'}</td>
                </tr>
            </table>

            <p>Please take one of the following actions:</p>
            <div style="margin: 20px 0;">
                <a href="{approval_link}" style="background-color: #27ae60; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">Approve Tool</a>
                <a href="{rejection_link}" style="background-color: #c0392b; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block; margin-left: 10px;">Reject Tool</a>
            </div>

            <p>Thank you for your attention to this submission.</p>
            <p>Best regards,<br><strong>AIHubCentral Team</strong></p>
            <hr style="border: 0; border-top: 1px solid #eee;">
            <p style="font-size: 12px; color: #777;">This is an automated email. Please do not reply directly to this message.</p>
        </body>
        </html>
        """

        # Send the email
        msg = Message(
            subject=f"New AI Tool Submission: {tool_name}",
            sender=os.getenv("MAIL_DEFAULT_SENDER"),  # Admin email as sender
            recipients=[os.getenv("MAIL_DEFAULT_SENDER")],  # Admin email as recipient
            html=email_body  # Use HTML body
        )
        mail.send(msg)

        return "Success", 200  # Return simple success message for AJAX
    except Exception as e:
        print(f"Email error: {str(e)}")
        return str(e), 500
@app.route("/approve")
def approve():
    try:
        tool_name = unquote(request.args.get("tool"))
        category = unquote(request.args.get("cat"))
        description = unquote(request.args.get("desc"))
        price = unquote(request.args.get("price"))
        website = unquote(request.args.get("web"))
        email = unquote(request.args.get("email"))
        tags = unquote(request.args.get("tags", ""))
        image_url = unquote(request.args.get("image_url", ""))

        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO ai_tools 
            (tool_name, category, description, price, website, email, image_url, tags) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (tool_name, category, description, price, website, email, image_url, tags))
        
        # Clear session data
        session.pop('image_data', None)
        
        conn.commit()
        conn.close()

        # Notify user of approval
        verification_msg = Message(
            "Your AI Tool Has Been Approved! ✅",
            recipients=[email],
            body=f"""
            Congratulations! 🎉 Your AI tool '{tool_name}' has been approved and is now listed on our website.

            ✅ Your email has been verified.
            🖥️ You can view your tool here: {APP_URL}/

            Thank you for contributing to the AI Hub!
            """
        )
        mail.send(verification_msg)

        return f"Tool '{tool_name}' approved, added to database, and user notified!", 200
    except Exception as e:
        return f"Error: {str(e)}", 500

@app.route("/reject")
def reject():
    try:
        tool_name = unquote(request.args.get("tool"))
        email = unquote(request.args.get("email"))

        # Notify user of rejection
        msg = Message("Your AI Tool Submission Was Rejected", recipients=[email],
                      body=f"Sorry, your tool '{tool_name}' was not approved.")
        mail.send(msg)

        return f"Tool '{tool_name}' has been rejected and user notified.", 200
    except Exception as e:
        return f"Error: {str(e)}", 500

@app.route("/filter_tools", methods=["POST"])
def filter_tools():
    try:
        data = request.json
        category = data.get("category")
        price = data.get("price")
        search_term = data.get("search", "")
        
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        
        # Build the query based on filters
        query = "SELECT * FROM ai_tools WHERE 1=1"
        params = []
        
        if category and category != "All Categories":
            # Use case-insensitive comparison for category matching
            query += " AND LOWER(category) = LOWER(%s)"
            params.append(category)
            
        if price and price != "All Pricing":
            query += " AND price = %s"
            params.append(price)
            
        if search_term:
            query += " AND (tool_name LIKE %s OR description LIKE %s OR tags LIKE %s)"
            search_param = f"%{search_term}%"
            params.extend([search_param, search_param, search_param])
            
        query += " ORDER BY tool_name"
        
        cursor.execute(query, params)
        tools = cursor.fetchall()
        
        # Convert binary image data to base64 for display if needed
        for tool in tools:
            if tool.get('image_file') and tool['image_file'] is not None:
                try:
                    tool['image_file'] = base64.b64encode(tool['image_file']).decode('utf-8')
                except Exception as e:
                    print(f"Error encoding image: {e}")
                    tool['image_file'] = None
        
        conn.close()
        
        # Return filtered tools as JSON
        return jsonify({"tools": tools})
    except Exception as e:
        print(f"Filter error: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
