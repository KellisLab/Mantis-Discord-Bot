import discord
import time
from openai import OpenAI
from config import OPENAI_API_KEY, ASSISTANT_ID

def setup(bot):
    """Register AI commands with the bot."""
    bot.tree.add_command(ask_manolis_gpt)
    bot.add_listener(on_message_reply, 'on_message')

async def on_message_reply(message):
    """Handle replies to Manolis GPT responses."""
    # Ignore messages from bots
    if message.author.bot:
        return
    
    # Check if this is a reply to a message
    if not message.reference or not message.reference.message_id:
        return
    
    try:
        # Get the message being replied to
        channel = message.channel
        replied_message = await channel.fetch_message(message.reference.message_id)
        
        # Check if the replied message is from our bot and contains a Manolis GPT response
        if (replied_message.author.bot and 
            replied_message.embeds and 
            (replied_message.embeds[0].title == "Manolis GPT Response" or 
             replied_message.embeds[0].title == "Manolis GPT Response (Contextual)")):
            
            # Build the full conversation chain
            conversation_chain = await build_conversation_chain(channel, replied_message)
            
            # Get the new question from the reply
            new_question = message.content.strip()
            
            # Create contextual prompt with full conversation history
            contextual_prompt = f"""Previous conversation:\n{conversation_chain}\n\nFollow-up question: {new_question}\n\nPlease answer the follow-up question, taking into account the full conversation context above."""
            
            # Send typing indicator
            async with channel.typing():
                # Get response from OpenAI assistant with context
                response = await get_assistant_response(contextual_prompt)
                
                if response:
                    # Check if response is an error message
                    if response.startswith("ERROR:"):
                        error_msg = response[6:].strip()  # Remove "ERROR:" prefix
                        await message.reply(f"❌ {error_msg}", mention_author=False)
                    else:
                        # Create embed for the contextual response
                        embed = discord.Embed(
                            title="Manolis GPT Response (Contextual)",
                            description=f"**❓ Question:** {new_question}\n\n**💭 Response:**\n{response}",
                            color=discord.Color.green(),
                        )
                        embed.set_footer(text="Mantis AI Cognitive Cartography")
                        
                        # Reply to the user's message
                        await message.reply(embed=embed)
                else:
                    await message.reply("❌ Unexpected error: No response received from the assistant.", mention_author=False)
    
    except Exception as e:
        print(f"Error handling message reply: {e}")
        # Silently fail to avoid spamming errors

async def build_conversation_chain(channel, current_message):
    """Build the full conversation chain by following reply references backwards."""
    conversation = []
    
    try:
        # Start with the current message and work backwards
        message = current_message
        depth = 0
        max_depth = 20  # Prevent infinite loops
        
        while message and depth < max_depth:
            # Extract Q&A from Manolis GPT responses
            if (message.author.bot and message.embeds and 
                len(message.embeds) > 0 and
                "Manolis GPT Response" in message.embeds[0].title):
                
                embed = message.embeds[0]
                description = embed.description
                
                # Parse the question and response from the embed
                if "**❓ Question:**" in description and "**💭 Response:**" in description:
                    try:
                        # Split on the response marker
                        parts = description.split("**💭 Response:**", 1)
                        if len(parts) >= 2:
                            # Extract question (remove any prefixes and clean up)
                            question_part = parts[0]
                            # Remove any follow-up markers and get just the question
                            if "**❓ Question:**" in question_part:
                                question = question_part.split("**❓ Question:**")[-1].strip()
                            else:
                                question = question_part.strip()
                            
                            # Remove any remaining formatting
                            question = question.replace("**🔗 Follow-up to:**", "").strip()
                            if question.startswith("*") and question.count("*") >= 2:
                                # Remove any markdown formatting that might be left
                                question = question.split("**")[-1].strip()
                            
                            response = parts[1].strip()
                            
                            if question and response:
                                conversation.append(f"Q: {question}\nA: {response}")
                    except Exception as e:
                        print(f"Error parsing Q&A: {e}")
            
            # Follow the reply chain backwards
            if message.reference and message.reference.message_id:
                try:
                    prev_message = await channel.fetch_message(message.reference.message_id)
                    
                    # Continue following the chain regardless of who sent it
                    # We'll filter for Manolis responses in the next iteration
                    message = prev_message
                    depth += 1
                except Exception as e:
                    print(f"Error fetching referenced message: {e}")
                    break
            else:
                break
    
    except Exception as e:
        print(f"Error building conversation chain: {e}")
    
    # Reverse to get chronological order
    conversation.reverse()
    return "\n\n".join(conversation)

async def get_assistant_response(question):
    """Helper function to get response from OpenAI assistant."""
    try:
        # Initialize OpenAI client
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # Check if assistant ID is configured
        if not ASSISTANT_ID:
            return "ERROR: Assistant ID is not configured. Please check the bot configuration."
        
        # Create a new thread for this conversation
        thread = client.beta.threads.create()
        
        # Add the question to the thread
        client.beta.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content=question,
        )
        
        # Create and start the run
        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=ASSISTANT_ID,
        )
        
        # Wait for the run to complete with polling
        max_wait_time = 30  # Maximum wait time in seconds
        start_time = time.time()
        
        while run.status in ["queued", "in_progress"]:
            if time.time() - start_time > max_wait_time:
                return "ERROR: Request timed out after 30 seconds. The AI assistant may be experiencing high load."
            
            time.sleep(1)  # Wait 1 second before checking again
            run = client.beta.threads.runs.retrieve(
                thread_id=thread.id,
                run_id=run.id,
            )
        
        # Check if the run completed successfully
        if run.status == "completed":
            # Get the assistant's response
            messages = client.beta.threads.messages.list(thread_id=thread.id)
            
            # Get the latest assistant message
            for message in messages.data:
                if message.role == "assistant" and message.content:
                    response_text = message.content[0].text.value
                    
                    # Discord has a 2000 character limit for embeds
                    if len(response_text) > 1500:  # Leave room for other embed content
                        response_text = response_text[:1497] + "..."
                    
                    return response_text
        
        return f"ERROR: Assistant run failed with status: {run.status}"
    
    except Exception as e:
        print(f"Error getting assistant response: {e}")
        return f"ERROR: {str(e)}"

@discord.app_commands.command(name="manolis", description="Ask Manolis GPT a question.")
async def ask_manolis_gpt(interaction: discord.Interaction, question: str):
    """Ask Manolis GPT a question using OpenAI Assistant API."""
    # Defer the response since OpenAI API calls can take time
    await interaction.response.defer()
    
    try:
        # Get response from assistant
        response_text = await get_assistant_response(question)
        
        if response_text:
            # Check if response is an error message
            if response_text.startswith("ERROR:"):
                error_msg = response_text[6:].strip()  # Remove "ERROR:" prefix
                await interaction.followup.send(f"❌ {error_msg}", ephemeral=True)
            else:
                # Create an embed for a nicer response
                embed = discord.Embed(
                    title="Manolis GPT Response",
                    description=f"**❓ Question:** {question}\n\n**💭 Response:**\n{response_text}",
                    color=discord.Color.blue(),
                )
                embed.set_footer(text="Mantis AI Cognitive Cartography")
                
                await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send("❌ Unexpected error: No response received from the assistant.", ephemeral=True)
    
    except Exception as e:
        await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)