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
intents.messages = True
intents.reactions = True
bot = commands.Bot(command_prefix='!', intents=intents)

MAX_MESSAGE_LENGTH = 2000
BOT_STREAMS = True

from public_modes import SYSTEM_MESSAGES_PUBLIC_OBFUSCATE, SYSTEM_MESSAGES_PUBLIC_NORMAL
try:
    from root_modes import SYSTEM_MESSAGES_ROOT_OBFUSCATE, SYSTEM_MESSAGES_ROOT_NORMAL
except:
    SYSTEM_MESSAGES_ROOT_OBFUSCATE = {}
    SYSTEM_MESSAGES_ROOT_NORMAL = {}

SYSTEM_MESSAGES = {**SYSTEM_MESSAGES_PUBLIC_OBFUSCATE, **SYSTEM_MESSAGES_PUBLIC_NORMAL, **SYSTEM_MESSAGES_ROOT_OBFUSCATE, **SYSTEM_MESSAGES_ROOT_NORMAL}
SYSTEM_MESSAGES_OBFUSCATE = {**SYSTEM_MESSAGES_PUBLIC_OBFUSCATE, **SYSTEM_MESSAGES_ROOT_OBFUSCATE}
STREAM_EXCEPTIONS = list(SYSTEM_MESSAGES_PUBLIC_OBFUSCATE.keys()) + list(SYSTEM_MESSAGES_ROOT_OBFUSCATE.keys()) + ["/timestamp"]
ARG_LIST = {"--stream": True, "--force-truncate": False, "--read-server": 0}

last_response_time = 0  # rate-limiting variable for public users

@bot.event
async def on_message(message):
    global last_response_time

    if message.author == bot.user:
        return

    if bot.user in message.mentions:
        current_time = time.time()
        if message.guild.name == "Cyborgism" and message.channel.name not in ["gpt-4-faraday-cage", "4-box"] and (current_time - last_response_time) < 30:
            return  # rate-limiting to 30 seconds between generation and next prompt
        print("Request received!")

        await message.add_reaction('\N{HOURGLASS}')

        try:
            utils.log_request(message)
            api_key = next(api_key_cycle)

            input_content = message.content.replace(f'<@{bot.user.id}>', '').strip()
            thread = isinstance(message.channel, discord.channel.Thread)

            if input_content == "/run-test-suite":
                await utils.test_suite(message, MAX_MESSAGE_LENGTH, SYSTEM_MESSAGES, ARG_LIST, api_key, bot)
                return
            if input_content.startswith("/help"):
                await utils.handle_help(message, MAX_MESSAGE_LENGTH, bot)
                return

            if message.attachments:
                input_content = await utils.read_attachments(message, input_content)

            keyword, args, user_msg = utils.parse_input_content(input_content, SYSTEM_MESSAGES, ARG_LIST)

            BOT_STREAMS = args["--stream"]

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
                if messages[-1]["role"] == "system":
                    messages.insert(0, messages.pop(-1))
            else:
                last_messages = list(reversed(await utils.get_last_n_messages(message, args["--read-server"])))
                content = '\n\n'.join([list(d.values())[0] for d in last_messages])
                if args["--read-server"]:
                    user_msg = content + "\n\n" + user_msg
                messages.append({"role": "user", "content": user_msg})

            num_tokens = utils.num_tokens_from_messages(messages)
            if num_tokens > 8100 or (len(messages) == 1 and messages[0]["role"] == "system") or messages == []:  # thread_history() automatically cuts off if too big, check if nothing survived it
                if args["--force-truncate"]:
                    messages = utils.truncate_user_content(messages, num_tokens - 7700)
                    num_tokens = 7700
                    MAX_TOKENS = 480
                else:
                    await utils.handle_error(message, f"Sorry, your prompt is too large by {num_tokens - 8100} tokens. Please give me a smaller one :)", thread, bot)
            else:
                print(f"Number of tokens in the prompt: {num_tokens}")
                MAX_TOKENS = 8180 - num_tokens  # 8192 as the default GPT-4 token limit, sometimes num_tokens isn't exact, hence leeway; also 8192 exactly triggers an error

            # if BOT_STREAMS set to False, or if keyword is one that requires modification after generation, no streaming
            if keyword in STREAM_EXCEPTIONS or not BOT_STREAMS:
                try:
                    completion = await utils.create_response(api_key, messages, MAX_TOKENS)
                    response = completion.choices[0].message.content
                except Exception as e:
                    await utils.response_errors(e, message, thread, bot)
                    return

                utils.log(message, messages, response, completion)

                if keyword == "/timestamp":
                    await message.reply(f"<t:{utils.convert_to_unix(response)}:t>")
                    await message.remove_reaction('\N{HOURGLASS}', bot.user)
                    return
                if keyword in SYSTEM_MESSAGES_OBFUSCATE:
                    response = await utils.de_obfuscate(api_key, keyword, response)
                    if response == -1:
                        await utils.handle_error(message, "An error occurred while de-obfuscating the text, please check my logs!", thread, bot)
                        return

                await utils.bot_reply(response, message, input_content, thread, MAX_MESSAGE_LENGTH)
            # streaming the output
            else:
                try:
                    response_stream = await utils.create_response(api_key, messages, MAX_TOKENS, stream=True)
                except Exception as e:
                    await utils.response_errors(e, message, thread, bot)
                    return

                await utils.bot_reply_stream(response_stream, message, messages, input_content, thread, MAX_MESSAGE_LENGTH)

            await message.remove_reaction('\N{HOURGLASS}', bot.user)
            last_response_time = current_time
        except Exception as e:
            print(traceback.format_exc())
            await utils.handle_error(message, "Something *very* unexpected has happened, please check my logs", False, bot)
            return

bot.run(DISCORD_TOKEN)