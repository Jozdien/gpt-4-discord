# Instructions for local usage

First add your OpenAI API key to a file titled `api-key.txt`, and your Discord token to a file titled `discord-token.txt`.

## Installing dependencies

```bash
pip install openai
pip install discord
pip install bs4
pip install tiktoken
```

## Using the bot

For more detailed usage on how to use the bot, I suggest running it and sending the message `@GPT-4 /help`.

# TO-DO

- Test suite
- Multi-message prompt chaining
- Stream outputs(?)
- Count tokens in-script
- Currently if the message gets deleted before output message is sent, just throws an error.

## To-do modes

- Generate information and put it into code that converts into graphs