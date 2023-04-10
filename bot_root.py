import os
import time
import utils
import discord
import itertools
import traceback
from discord.ext import commands

with open("discord-token.txt", "r") as f:
    DISCORD_TOKEN = f.read().strip()
API_KEYS = utils.read_file_to_list('./api-key.txt')
api_key_cycle = itertools.cycle(API_KEYS)  # Cycle through different API keys for rate limiting reasons

intents = discord.Intents.default()
intents.typing = False
intents.presences = False
bot = commands.Bot(command_prefix='!', intents=intents)

MAX_MESSAGE_LENGTH = 2000
BOT_STREAMS = True

from public_modes import SYSTEM_MESSAGES_PUBLIC
try:
    from root_modes import SYSTEM_MESSAGES_ROOT_OBFUSCATE, SYSTEM_MESSAGES_ROOT_NORMAL
except:
    SYSTEM_MESSAGES_ROOT_OBFUSCATE = {}
    SYSTEM_MESSAGES_ROOT_NORMAL = {}

SYSTEM_MESSAGES = {**SYSTEM_MESSAGES_PUBLIC, **SYSTEM_MESSAGES_ROOT_OBFUSCATE, **SYSTEM_MESSAGES_ROOT_NORMAL}

last_response_time = 0  # rate-limiting variable for public users

@bot.event
async def on_message(message):
    global last_response_time

    if message.author == bot.user:
        return

    if bot.user in message.mentions:
        current_time = time.time()
        if message.guild.name == "Cyborgism" and message.channel.name != "gpt-4-faraday-cage" and (current_time - last_response_time) < 30:
            return  # rate-limiting to 30 seconds between generation and next prompt
        print("Request received!")

        await message.add_reaction('\N{HOURGLASS}')

        try:
            utils.log_request(message)
            api_key = next(api_key_cycle)

            input_content = message.content.replace(f'<@{bot.user.id}>', '').strip()

            if input_content == "/run-test-suite":
                await utils.test_suite(message, MAX_MESSAGE_LENGTH, SYSTEM_MESSAGES, api_key, bot)
                return
            if input_content.startswith("/help"):
                await utils.handle_help(message, MAX_MESSAGE_LENGTH, bot)
                return

            thread = isinstance(message.channel, discord.channel.Thread)

            if message.attachments:
                input_content = await utils.read_attachments(message, input_content)

            keyword, user_msg = utils.parse_input_content(input_content, SYSTEM_MESSAGES)

            messages = []
            if keyword:
                messages.append({"role": "system", "content": SYSTEM_MESSAGES[keyword]})
            if keyword == "/dev-aware":  # a mode that allows the bot access to its own source code
                with open("bot_root.py", "r") as f:
                    source_code = f.read()
                messages[0]["content"] = messages[0]["content"].format(source_code=source_code)
            if keyword == "/lw":
                user_msg = utils.process_lw(user_msg)
                if user_msg == -1:
                    await utils.handle_error(message, "Please give me a link to summarize, I cannot read your messages otherwise :(", thread, bot)
                    return
                elif user_msg == -2:
                    await utils.handle_error(message, "Sorry, the web parser failed, please try again!", thread, bot)
                    return

            if thread:
                messages = await utils.thread_history(messages, message, bot)
                messages.reverse()  # thread history received in reversed order
            else:
                messages.append({"role": "user", "content": user_msg})

            num_tokens = utils.num_tokens_from_messages(messages)
            if num_tokens > 8100 or (len(messages) == 1 and messages[0]["role"] == "system") or messages == []:  # thread_history() automatically cuts off if too big, check if nothing survived it
                await utils.handle_error(message, f"Sorry, your prompt is too large by {num_tokens - 8100} tokens. Please give me a smaller one :)", thread, bot)
            else:
                print(f"Number of tokens in the prompt: {num_tokens}")
                MAX_TOKENS = 8180 - num_tokens  # 8192 as the default GPT-4 token limit, sometimes num_tokens isn't exact, hence leeway; also 8192 exactly triggers an error

                        # if BOT_STREAMS is set to False, or if the keyword is any of these commands, which forces us to modify the completion after it has been received in full,
            # we use the former method without streams. Otherwise (see the else:), we stream the completion.
            if (keyword == "/timestamp") or \
                (keyword in ["/no-filter", "/no-filter-hard", "/no-filter-conv", "/no-filter-role", "/no-filter-stack"]) or \
                (keyword in SYSTEM_MESSAGES_ROOT_OBFUSCATE) or \
                (not BOT_STREAMS):


                try:
                    completion = utils.create_response(api_key, messages, MAX_TOKENS)
                except Exception as e:
                    # No multimodal access to GPT-4
                    if repr(e) == "TypeError('Object of type bytes is not JSON serializable')":
                        await utils.handle_error(message, "Sorry, you don't have multimodal access with me yet.", thread, bot)
                        return
                    # Rate limiting
                    if repr(e) == "RateLimitError(message='The server had an error while processing your request. Sorry about that!', http_status=429, request_id=None)":
                        await utils.handle_error(message, "Sorry, you're sending a lot of requests, I need to cool down. Please resend your request after a few seconds!", thread, bot)
                        return
                    print(traceback.format_exc())
                    await utils.handle_error(message, "An error has occurred while generating the response, please check my logs!", thread, bot)
                    return

                response = completion.choices[0].message.content

                utils.log(message, messages, response, completion)

                if keyword == "/timestamp":
                    await message.reply(f"<t:{utils.convert_to_unix(response)}:t>")
                    await message.remove_reaction('\N{HOURGLASS}', bot.user)
                    return
                if keyword in ["/no-filter", "/no-filter-hard", "/no-filter-conv", "/no-filter-role", "/no-filter-stack"] or keyword in SYSTEM_MESSAGES_ROOT_OBFUSCATE:
                    response = utils.de_obfuscate(api_key, keyword, response)
                    if response == -1:
                        await utils.handle_error(message, "An error occurred while de-obfuscating the text, please check my logs!", thread, bot)
                        return

                if thread:
                    thread = message.channel
                for i in range(0, len(response), MAX_MESSAGE_LENGTH):
                    if i == 0:
                        sent_message = await message.reply(response[i:i + MAX_MESSAGE_LENGTH])
                    else:
                        if not thread:
                            thread = await sent_message.create_thread(name=input_content[:100], auto_archive_duration=60)
                        await thread.send(response[i:i + MAX_MESSAGE_LENGTH])

            else: #we are executing a command with streaming, and it
                  # requires no intermediate verification (ie it can be sent
                  # to the user as-is)
                response = utils.create_response(api_key, messages, MAX_TOKENS, stream=True)

                last_message_content = ""
                message_to_add = ""
                completion = "" #the total response once the stream terminates, for logging purposes
                

                last_change_time = time.time()
                change_freq = 1 #changes every second. Limited to 5 edits per 5 seconds, so can't go lower than this.


                thread_exists = isinstance(message.channel, discord.channel.Thread)
                if thread_exists:
                    thread = message.channel

                """
                last_message: discord.Message object
                message_to_add: the message we are building up that we will want to add to the last message
                last_message_content: the content of last_message

                response is a generator that progressively receives more tokens from openAI.

                We first add the first token to a reply message:
                """
                #the first element is an empty string, the second is the first token. Can't have an empty completion so this is OK
                message_to_add += next(response)['choices'][0]['delta'].get('content', '') #empty because it is an assistant delta, not a content one
                message_to_add += next(response)['choices'][0]['delta'].get('content', '')
                last_message = await message.reply(message_to_add)
                message_to_add, last_message_content = "", message_to_add

                """
                Now, we loop through all subsequent events in the generator, and:
                    we get the event_token with event['choices'][0]['delta'].get('content', '')
                    we check if we should create a new message depending on whether the length of last_message_content + message_to_add + event_token exceeds MAX_MESSAGE_CHAR_LENGTH.
                        if so, we create a new message for the event_token by itself and update the last_change_time, and edit the last_message to include the message_to_add. 
                        but if there has been no thread created yet, we create a new thread under the first message to put this event_token.
                    if we shouldn't create a new message, we only edit the current message with the new message_to_add thing, which we don't do everytime: 
                        we only do it when time.time() - last_change_time >= change_freq.
                once the loop through the generator is over, we have a remaining message_to_add, which we know fits into the last_message, so we add it.
                """

                # Loop through subsequent generator events
                for event in response:
                    time.sleep(0.01)
                    event_token = event['choices'][0]['delta'].get('content', '')
                    completion += event_token
                    #print(event_token, end='', flush=True)

                    if len(last_message_content + message_to_add + event_token) > MAX_MESSAGE_LENGTH:
                        # Create a new message
                        if not thread_exists:
                            thread = await last_message.channel.create_thread(
                                name = user_msg[:50], #idk if there is a limit!
                                message = last_message,
                                auto_archive_duration = 60
                                )
                            thread_exists = True
                        await last_message.edit(content=last_message_content + message_to_add)
                        #print(message_to_add, flush=True)
                        last_message = await thread.send(event_token)
                        #print(event_token, end='', flush=True)
                        message_to_add, last_message_content = "", event_token
                        last_change_time = time.time()

                    else:
                        message_to_add += event_token
                        if time.time() - last_change_time >= change_freq:
                            await last_message.edit(content=last_message_content + message_to_add)
                            #print(message_to_add, end='', flush=True)
                            message_to_add, last_message_content = "", last_message_content + message_to_add
                            last_change_time = time.time()

                if message_to_add != "":
                    await last_message.edit(content=last_message_content + message_to_add)
                    #print(message_to_add, flush=True)
                
                # risky if something fails, but alternative is to log 
                # everything while the stream is going on, which would
                # appear to require changing the log function significantly
                utils.log(message, messages, response, completion) 

            await message.remove_reaction('\N{HOURGLASS}', bot.user)
            last_response_time = current_time
        except Exception as e:
            print(traceback.format_exc())
            await utils.handle_error(message, "Something *very* unexpected has happened, please check my logs", False, bot)
            return

bot.run(DISCORD_TOKEN)