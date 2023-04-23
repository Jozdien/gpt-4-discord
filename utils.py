import re
import time
import openai
import requests
import datetime
import tiktoken
import traceback
from bs4 import BeautifulSoup

# openai.api_key_path = './api-key.txt'

def read_file_to_list(file_name):
    with open(file_name, 'r') as file:
        lines = file.readlines()
    return [line.strip() for line in lines]

def create_response(api_key, messages, MAX_TOKENS, model="gpt-4", stream = False):
    openai.api_key = api_key
    completion = openai.ChatCompletion.create(
        model=model,
        messages=messages,
        max_tokens=MAX_TOKENS,
        stream=stream
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

async def test_suite(message, MAX_MESSAGE_LENGTH, SYSTEM_MESSAGES, api_key, bot):
    try:
        tests = {}
        tests["Help test"] = await handle_help(message, MAX_MESSAGE_LENGTH, bot, test=True)
        tests["Attachments test"] = await read_attachments(message, "", test=True)
        tests["Parse input content w/o keyword for system message"] = parse_input_content("/help", SYSTEM_MESSAGES, test=True)
        tests["Parse input content with keyword for system message"] = parse_input_content("/dev Fix this hacky code", SYSTEM_MESSAGES, test=True)
        tests["LessWrong test"] = process_lw("https://www.lesswrong.com/posts/kXAb5riiaJNrfR8v8/the-ritual", test=True)
        tests["GPT-4 test"] = test_api(api_key, model="gpt-4")
        tests["GPT-3.5-turbo test"] = test_api(api_key, model="gpt-3.5-turbo")
        tests["Timestamp test"] = test_timestamp(api_key)
        tests["De-obfuscate test"] = de_obfuscate(api_key, "", "Why did the chicken 🐓 cross the road? 🚧 To get to the funny 😂 side, of course! 🎉", test=True)
        if all(tests.values()):
            await message.reply("All tests passed!")
        else:
            failed_tests = [key for key, value in tests.items() if value is False]
            await message.reply("The following tests have failed:\n" + "".join([item + "\n" for item in failed_tests]))
    except Exception as e:
        print(traceback.format_exc())
        await message.reply("Something isn't working, check my logs.")
    await message.remove_reaction('\N{HOURGLASS}', bot.user)
    return

async def handle_help(message, MAX_MESSAGE_LENGTH, bot, test=False):
    with open('instructions.md', "r") as file:
        content = file.read()
    if test:
        return True
    for i in range(0, len(content), MAX_MESSAGE_LENGTH):
        await message.channel.send(content[i:i + MAX_MESSAGE_LENGTH])
    await message.remove_reaction('\N{HOURGLASS}', bot.user)
    return

async def handle_error(message, err_msg, thread, bot):
    await message.reply(err_msg)
    await message.remove_reaction('\N{HOURGLASS}', bot.user)
    await message.add_reaction('❌')
    if thread:
        await message.channel.edit(locked=True, archived=True)

async def read_attachments(message, input_content, test=False):
    if message.attachments:
        for attachment in message.attachments:
            # Check if attachment is a text file
            if attachment.filename.endswith('.txt'):
                file_content = await attachment.read()
                input_content = f"{input_content}\n\n{file_content.decode('utf-8')}"
            # If attachment is an image, for if we have multimodal GPT-4
            else:
                image_bytes = await attachment.read()
                input_content.append({"image": image_bytes})
    if test:
        return True
    return input_content

def parse_input_content(input_content, SYSTEM_MESSAGES, test=False):
    keyword, user_msg = None, input_content
    if input_content in SYSTEM_MESSAGES:
        return input_content, ""
    if " " in input_content and input_content.split(" ")[0] in SYSTEM_MESSAGES:
        keyword, user_msg = input_content.split(" ", 1)
    if test:
        return True
    return keyword, user_msg

async def response_errors(e, thread, bot):
    # No multimodal access to GPT-4
    if repr(e) == "TypeError('Object of type bytes is not JSON serializable')":
        await handle_error(message, "Sorry, you don't have multimodal access with me yet.", thread, bot)
        return
    # Rate limiting
    if repr(e) == "RateLimitError(message='The server had an error while processing your request. Sorry about that!', http_status=429, request_id=None)":
        await handle_error(message, "Sorry, you're sending a lot of requests, I need to cool down. Please resend your request after a few seconds!", thread, bot)
        return
    print(traceback.format_exc())
    await handle_error(message, "An error has occurred while generating the response, please check my logs!", thread, bot)
    return

async def bot_reply(response, message, input_content, thread, MAX_MESSAGE_LENGTH):
    if thread:
        thread = message.channel
    for i in range(0, len(response), MAX_MESSAGE_LENGTH):
        if i == 0:
            sent_message = await message.reply(response[i:i + MAX_MESSAGE_LENGTH])
        else:
            if not thread:
                thread = await sent_message.create_thread(name=input_content[:100], auto_archive_duration=60)
            await thread.send(response[i:i + MAX_MESSAGE_LENGTH])
    return

async def bot_reply_stream(response_stream, message, messages, input_content, thread, MAX_MESSAGE_LENGTH):
    last_change_time = time.time()
    change_freq = 1  # changes every second; limited to 5 edits per 5 seconds, so can't go lower than this

    """
    last_message => discord.Message object
    message_to_add => the message we are building up between edits that we will want to add to the last message
    last_message_content => the content of last_message

    response => generator that progressively receives more generated tokens
    """
    # the first element is an empty string, the second is the first token. Can't have an empty completion so this is OK
    stream_event = next(response_stream)
    log_stream(stream_event, new=True)
    stream_log = [stream_event]
    message_to_add = "" + stream_event['choices'][0]['delta'].get('content', '')  # empty because it is an assistant delta, not a content one

    stream_event = next(response_stream)
    log_stream(stream_event)
    stream_log.append(stream_event)
    message_to_add += stream_event['choices'][0]['delta'].get('content', '')

    completion = message_to_add  # the total response once the stream terminates, for logging purposes
    last_message = await message.reply(message_to_add)
    message_to_add, last_message_content = "", message_to_add

    """
    Now, we loop through all subsequent events in the generator, and:
        we get the event_token with event['choices'][0]['delta'].get('content', '')
        we check if we should create a new message depending on whether the length of last_message_content + message_to_add + event_token exceeds MAX_MESSAGE_LENGTH.
            if so, we create a new message for the event_token by itself and update the last_change_time, and edit the last_message to include the message_to_add. 
            but if there has been no thread created yet, we create a new thread under the first message to put this event_token.
        if we shouldn't create a new message, we only edit the current message with the new message_to_add thing, which we don't do everytime: 
            we only do it when time.time() - last_change_time >= change_freq.
    once the loop through the generator is over, we have a remaining message_to_add, which we know fits into the last_message, so we add it.
    """
    for stream_event in response_stream:
        log_stream(stream_event)
        stream_log.append(stream_event)
        time.sleep(0.01)
        event_token = stream_event['choices'][0]['delta'].get('content', '')
        completion += event_token

        if len(last_message_content + message_to_add + event_token) > MAX_MESSAGE_LENGTH:
            if not thread:
                thread = await last_message.create_thread(name = input_content[:100], auto_archive_duration = 60)
            await last_message.edit(content=last_message_content + message_to_add)
            last_message = await thread.send(event_token)
            message_to_add, last_message_content = "", event_token
            last_change_time = time.time()
        else:
            message_to_add += event_token
            if time.time() - last_change_time >= change_freq:
                await last_message.edit(content=last_message_content + message_to_add)
                message_to_add, last_message_content = "", last_message_content + message_to_add
                last_change_time = time.time()

    if message_to_add != "":
        await last_message.edit(content=last_message_content + message_to_add)

    log(message, messages, completion, stream_log, stream=True)

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

def de_obfuscate(api_key, keyword, response, test=False):
    deobfuscated_response = ""
    try:
        # turbo => 4097 token limit; setting cut-off as 6000 characters ~= 1500-2000 tokens for input
        response_lst = split_string(response, 6000)
        temp_response = ""
        for split_input in response_lst:
            content = f"Please remove the emojis from the following text and make it look cleaner:\n\n\"\"\"\n{split_input}\n\"\"\""
            messages = [{"role": "user", "content": content}]
            num_tokens = num_tokens_from_messages(messages, model='gpt-3.5-turbo')
            MAX_TOKENS = 4080 - num_tokens  # sometimes the num_token calculation isn't exact, hence leeway
            completion = create_response(api_key, messages, MAX_TOKENS, "gpt-3.5-turbo")
            temp_response += completion.choices[0].message.content
        response = temp_response
        deobfuscated_response += temp_response
    except Exception as e:
        print(traceback.format_exc())
        return -1
    if test:
        return True
    return deobfuscated_response

def log_request(message):
    user = message.author
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

    with open('message_log.txt', "a") as file:
        file.write("User: {0}\n\nTimestamp: {1}\n\nMessage\n```\n{2}\n```\n\n---\n\n".format(user, timestamp, message.content))

def log_stream(stream_event, new=False):
    mode = "w" if new else "a"
    with open('stream_log.txt', mode) as file:
        file.write("{0}\n\n".format(stream_event))

def log(message, messages, response, completion, stream=False):
    user = message.author
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    messages_str = "\n".join(str(element) for element in messages)
    if stream:
        completion = '\n'.join(str(x) for x in completion)

    with open('bot_log.txt', "a") as file:
        file.write("User: {0}\n\nTimestamp: {1}\n\nPrompt\n```\n{2}\n```\n\nGeneration\n```\n{3}\n```\n\n---\n\n".format(
            user, timestamp, messages_str, response))
    with open('full_log.txt', "a") as file:
        file.write("User: {0}\n\nTimestamp: {1}\n\nPrompt\n```\n{2}\n```\n\nGeneration\n```\n{3}\n```\n\nServer request\n```\n{4}\n```\n\n---\n\n".format(
            user, timestamp, messages_str, response, completion))

async def thread_history(messages, message, bot):
    num_tokens = num_tokens_from_messages(messages)
    async for thread_message in message.channel.history(limit=200):
        start_message_flag = False
        if str(thread_message.type) == "MessageType.thread_starter_message":  # by default the thread starter message's content returns an empty string
            thread_message = await message.channel.parent.fetch_message(message.channel.id)
            start_message_flag = True
        if thread_message.author == message.author:  # only taking the author's and the bot's messages into the context
            new_message = [{"role": "user", "content": thread_message.content}]
            if thread_message.content == '':
                continue
            if (num_tokens + num_tokens_from_messages(new_message)) > 8100:
                return messages
            messages += new_message
        elif thread_message.author == bot.user:
            new_message = [{"role": "assistant", "content": thread_message.content}]
            if (num_tokens + num_tokens_from_messages(new_message)) > 7500:
                return messages
            messages += new_message
            if str(thread_message.type) == "MessageType.reply":  # need the prompt for the bot response for proper context-setting
                parent = thread_message.reference.resolved
                if parent.author == message.author and not start_message_flag:  # prevent double adding of messages that were sent by the author, parent of start message won't get added
                    continue
                new_message = [{"role": "user", "content": parent.content}]
                if (num_tokens + num_tokens_from_messages(new_message)) > 7500:
                    return messages
                messages += new_message
        num_tokens = num_tokens_from_messages(messages)
    # system messages work better when they're leading the messages
    if messages[0]["role"] == "system":
        messages = messages[1:] + messages[:1]
    return messages

def process_lw(user_msg, test=False):
    try:
        url_str = re.search("(?P<url>https?://[^\s]+)", user_msg).group("url")
    except Exception as e:
        print(traceback.format_exc())
        return -1
    url = url_str.replace('lesswrong', 'greaterwrong').replace('alignmentforum.org', 'greaterwrong.com')
    try:
        x = requests.get(url)
    except Exception as e:
        print(traceback.format_exc())
        return -2
    html = x.content.decode('utf-8')
    soup = BeautifulSoup(cleanHtml(html), "html.parser")
    encoded_text = encode_html_as_text(soup)
    post_title = add_consistent_newlines(soup.select_one(".post-title").text.strip()[2:]).strip()
    author = soup.select_one(".author").text.strip()
    date = datetime.datetime.strptime(soup.select_one(".date").text.strip(), "%d %b %Y %H:%M %Z").strftime("%B %d %Y")
    content = add_consistent_newlines(soup.select_one(".body-text.post-body").text.strip())
    
    user_msg = user_msg.replace(url_str, '') + "\n\nTitle: {0}\nAuthor: {1}\nURL: {2}\nDate: {3}\nContent: {4}".format(post_title, author, url_str, date, content)
    if test:
        return True
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

def test_api(api_key, model):
    if model == "gpt-4":
        messages = [
            {"role": "system", "content": "You are an AI bot that only says the words \"Test successful.\""},
            {"role": "user", "content": "Test"}
        ]
    elif model == "gpt-3.5-turbo":
        messages = [{"role": "user", "content": "You are an AI bot that only says the words \"Test successful.\""}]
    completion = create_response(api_key, messages, 256, model)
    if completion.choices[0].message.content == "Test successful.":
        return True
    return False

def test_timestamp(api_key):
    system_message = (
        "You are an expert in timezone conversions.\n\n"
        "- You accept user inputs that contain information on a timezone (this could be \"IST\" for Indian Standard Time, \"-4:30\" for the corresponding UTC offset, "
        "or a geographic location such as \"Melbourne\"), as well a date and time in that timezone.\n"
        "- Convert this to UTC time.\n"
        "- If it is already in UTC time, don't convert it.\n"
        "- Reformat this time as 'YYYY-MM-DD HH:MM:SS', and output this alone.\n"
        "- Make sure that you don't output anything else but the info in the above format."
    )
    messages = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": "10:00 UTC, 20 February 2001"}
    ]
    completion = create_response(api_key, messages, 256)
    # This test incidentally only works if you're in a certain timezone! 
    # Otherwise, you can find the right one with something like https://www.epochconverter.com/
    if convert_to_unix(completion.choices[0].message.content) == 982663200:
        return True
    return False