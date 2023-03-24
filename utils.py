import re
import time
import openai
import requests
import datetime
from bs4 import BeautifulSoup

openai.api_key_path = './api-key.txt'

def create_response(messages, MAX_TOKENS, model="gpt-4"):
    completion = openai.ChatCompletion.create(
        model=model,
        messages=messages,
        max_tokens=MAX_TOKENS
    )
    return completion

async def handle_help(message, MAX_MESSAGE_LENGTH):
    with open('instructions.md', "r") as file:
        content = file.read()
    for i in range(0, len(content), MAX_MESSAGE_LENGTH):
        await message.channel.send(content[i:i + MAX_MESSAGE_LENGTH])
    return

def parse_input_content(input_content, SYSTEM_MESSAGES):
    keyword, user_msg = None, input_content
    if " " in input_content and input_content.split(" ")[0] in SYSTEM_MESSAGES:
        keyword, user_msg = input_content.split(" ", 1)
    
    return keyword, user_msg

def split_string(input_string, substring_length):
    substrings = []

    while input_string:
        if len(input_string) <= substring_length:
            substrings.append(input_string)
            input_string = ''
        else:
            last_space_position = input_string.rfind(' ', 0, substring_length)
            substrings.append(input_string[:last_space_position])
            input_string = input_string[last_space_position + 1:]

    return substrings

def de_obfuscate(keyword, response):
    deobfuscated_response = ""
    try:
        # turbo => 4097 token limit; setting cut-off as 6000 characters ~= 1500-2000 tokens for input
        response_lst = split_string(response, 6000)
        print(response_lst)
        temp_response = ""
        for split_input in response_lst:
            content = f"Please remove the emojis from the following text and make it look cleaner:\n\n\"\"\"\n{split_input}\n\"\"\""
            messages = [{"role": "user", "content": content}]
            completion = create_response(messages, 2000, "gpt-3.5-turbo")
            temp_response += completion.choices[0].message.content
        response = temp_response
        deobfuscated_response += temp_response
    except Exception as e:
        print(repr(e))
        return -1
            
    return deobfuscated_response

def log(message, messages, response, completion):
    user_name = message.author.name

    with open('bot_log.txt', "a") as file:
        file.write("User: {0}\n\nPrompt\n```\n{1}\n```\n\nGeneration\n```\n{2}\n```\n\nServer request\n```\n{3}\n```\n\n---\n\n".format(user_name, messages, response, completion))

def process_lw(user_msg):
    try:
        url_str = re.search("(?P<url>https?://[^\s]+)", user_msg).group("url")
    except Exception as e:
        print(repr(e))
        return -1
    url = url_str.replace('lesswrong', 'greaterwrong').replace('alignmentforum.org', 'greaterwrong.com')
    try:
        x = requests.get(url)
    except Exception as e:
        print(repr(e))
        return -2
    html = x.content.decode('utf-8')
    soup = BeautifulSoup(cleanHtml(html), "html.parser")
    encoded_text = encode_html_as_text(soup)
    post_title = add_consistent_newlines(soup.select_one(".post-title").text.strip()[2:]).strip()
    author = soup.select_one(".author").text.strip()
    date = datetime.datetime.strptime(soup.select_one(".date").text.strip(), "%d %b %Y %H:%M %Z").strftime("%B %d %Y")
    content = add_consistent_newlines(soup.select_one(".body-text.post-body").text.strip())
    
    user_msg = user_msg.replace(url_str, '') + "\n\nTitle: {0}\nAuthor: {1}\nURL: {2}\nDate: {3}\nContent: {4}".format(post_title, author, url_str, date, content)
    return user_msg

def cleanHtml(html):
    res = html
    res = re.sub("\u201c", '"', res)
    res = re.sub("\u201d", '"', res)
    res = re.sub("\u200b", '', res)
    # res = re.sub(r'http\S+', 'ʬ', res)
    return res

def encode_html_as_text(soup):
    # Convert different tags into text we would want GPT to learn
    # for a in soup.select('a'):
    #     a.insert(len(a), " ʬ")
    for li in soup.select("li"):
        li.insert(0, "&newline - ")
    for blockquote in soup.select("blockquote"):
        for child in blockquote.children:
            c = child
            if c.name != None:
                break
        try:
            c.insert(0, "> ")
        except:  # Has no nested children tags, just insert first
            blockquote.insert(0, "> ")
    for italics in soup.select("em"):
        italics.insert(len(italics), "*")
        italics.insert(0, "*")
    for italics in soup.select("i"):
        italics.insert(len(italics), "*")
        italics.insert(0, "*")
    for paragraphs in soup.select("p"):
        paragraphs.insert(len(paragraphs), "&newline")
    for headings in soup.select("h1"):
        headings.insert(len(headings), "&newline")
        headings.insert(0, "# ")
    for headings in soup.select("h2"):
        headings.insert(len(headings), "&newline")
        headings.insert(0, "## ")
    for headings in soup.select("h3"):
        headings.insert(len(headings), "&newline")
        headings.insert(0, "### ")
    for nav in soup.select("nav"):
        nav.insert(len(nav), "&newline")
    for bold in soup.select("b"):
        bold.insert(len(bold), "**")
        bold.insert(0, "**")
    for bold in soup.select("strong"):
        bold.insert(len(bold), "**")
        bold.insert(0, "**")
    # raw latex support
    for latex in soup.find_all("span", class_="mjx-math"):
        latex.string = ""
        latex.insert(0, latex.get("aria-label"))
    return  # insert is in-place, no need to return soup

def add_consistent_newlines(paragraph):
    # Add in Consistent Newlines
    paragraph = paragraph.replace("&newline", "\n")
    return paragraph

def convert_to_unix(date_time_string):
    date_time_obj = datetime.datetime.strptime(date_time_string, "%Y-%m-%d %H:%M:%S")
    unix_timestamp = time.mktime(date_time_obj.timetuple())
    return int(unix_timestamp)