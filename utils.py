import re
import time
import openai
import requests
import datetime
import tiktoken
from bs4 import BeautifulSoup

# openai.api_key_path = './api-key.txt'

def read_file_to_list(file_name):
    with open(file_name, 'r') as file:
        lines = file.readlines()
    return [line.strip() for line in lines]

def create_response(api_key, messages, MAX_TOKENS, model="gpt-4"):
    openai.api_key = api_key
    completion = openai.ChatCompletion.create(
        model=model,
        messages=messages,
        max_tokens=MAX_TOKENS
    )
    return completion

def num_tokens_from_messages(messages, model="gpt-4"):
    """Returns the number of tokens used by a list of messages."""
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        print("Warning: model not found. Using cl100k_base encoding.")
        encoding = tiktoken.get_encoding("cl100k_base")
    if model == "gpt-3.5-turbo-0301" or model == "gpt-3.5-turbo":
        tokens_per_message = 4  # every message follows <|start|>{role/name}\n{content}<|end|>\n
        tokens_per_name = -1  # if there's a name, the role is omitted
    elif model == "gpt-4-0314" or model == "gpt-4":
        tokens_per_message = 3
        tokens_per_name = 1
    else:
        print(f"""num_tokens_from_messages() is not implemented for model {model}.""")
        return -1
    num_tokens = 0
    for message in messages:
        num_tokens += tokens_per_message
        for key, value in message.items():
            num_tokens += len(encoding.encode(value))
            if key == "name":
                num_tokens += tokens_per_name
    num_tokens += 3  # every reply is primed with <|start|>assistant<|message|>
    return num_tokens

async def handle_help(message, MAX_MESSAGE_LENGTH, test=False):
    with open('instructions.md', "r") as file:
        content = file.read()
    if test:
        return
    for i in range(0, len(content), MAX_MESSAGE_LENGTH):
        await message.channel.send(content[i:i + MAX_MESSAGE_LENGTH])
    return

async def handle_error(message, err_msg, thread, bot, test=False):
    await message.reply(err_msg)
    await message.remove_reaction('\N{HOURGLASS}', bot.user)
    await message.add_reaction('❌')
    if thread:
        await message.channel.edit(locked=True, archived=True)

def parse_input_content(input_content, SYSTEM_MESSAGES):
    keyword, user_msg = None, input_content
    if input_content in SYSTEM_MESSAGES:
        return input_content, ""
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

def de_obfuscate(api_key, keyword, response):
    deobfuscated_response = ""
    try:
        # turbo => 4097 token limit; setting cut-off as 6000 characters ~= 1500-2000 tokens for input
        response_lst = split_string(response, 6000)
        temp_response = ""
        for split_input in response_lst:
            content = f"Please remove the emojis from the following text and make it look cleaner:\n\n\"\"\"\n{split_input}\n\"\"\""
            messages = [{"role": "user", "content": content}]
            num_tokens = num_tokens_from_messages(messages)
            MAX_TOKENS = 4096 - num_tokens
            completion = create_response(api_key, messages, MAX_TOKENS, "gpt-3.5-turbo")
            temp_response += completion.choices[0].message.content
        response = temp_response
        deobfuscated_response += temp_response
    except Exception as e:
        print(repr(e))
        return -1
            
    return deobfuscated_response

def log_request(message):
    user_name = message.author.name
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

    with open('message_log.txt', "a") as file:
        file.write("User: {0}\n\nTimestamp: {1}\n\nMessage\n```\n{2}\n```\n\n---\n\n".format(user_name, timestamp, message.content))

def log(message, messages, response, completion):
    user_name = message.author.name
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

    with open('bot_log.txt', "a") as file:
        file.write("User: {0}\n\nTimestamp: {1}\n\nPrompt\n```\n{2}\n```\n\nGeneration\n```\n{3}\n```\n\nServer request\n```\n{4}\n```\n\n---\n\n".format(user_name, timestamp, messages, response, completion))

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