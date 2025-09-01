import discord
from typing import Dict
from utils.github_update_manager import GitHubUpdateManager


def setup(bot):
    """Register the DM update handler with the bot."""
    bot.add_listener(on_dm_message, 'on_message')
    print("✅ DM update handler registered successfully!")


async def on_dm_message(message: discord.Message):
    """Handle DM messages from users who may have active update sessions."""
    # Only process DMs to the bot (not from the bot)
    if not isinstance(message.channel, discord.DMChannel):
        return
    
    if message.author.bot:
        return
    
    # Check if user has an active update session
    bot = message._state._get_client()
    update_manager = getattr(bot, 'github_update_manager', None)
    if not update_manager:
        return
    
    session = update_manager.get_session(message.author.id)
    if not session:
        return
    
    print(f"📱 Processing DM from {message.author.name} with active update session")
    
    try:
        await handle_update_conversation(message, update_manager, session)
    except Exception as e:
        print(f"❌ Error handling DM update conversation: {e}")
        await message.channel.send(
            "❌ Sorry, there was an error processing your update. "
            "Your session has been reset. Please wait for the next reminder or contact support."
        )
        update_manager.end_session(message.author.id)


async def handle_update_conversation(message: discord.Message, update_manager: GitHubUpdateManager, session: Dict):
    """Handle the conversation flow for updating GitHub issues/PRs."""
    stage = session.get("stage", "awaiting_initial_response")
    user_id = message.author.id

    stage_handlers = {
        "awaiting_initial_response": handle_initial_response,
        "awaiting_item_selection": handle_item_selection,
        "awaiting_item_selection_for_update": handle_item_selection_for_update,
        "awaiting_update_content": handle_update_content,
        "awaiting_continue_choice": handle_continue_choice,
        "awaiting_next_update_or_done": handle_next_update_or_done,
    }

    handler = stage_handlers.get(stage)
    if handler:
        await handler(message, update_manager, session)
    else:
        # Unknown stage, reset session
        await message.channel.send("❓ I'm not sure what stage we're at. Let me reset your session.")
        update_manager.end_session(user_id)


async def handle_initial_response(message: discord.Message, update_manager: GitHubUpdateManager, session: Dict):
    """Handle the user's initial response after receiving a reminder DM."""
    user_id = message.author.id
    update_items = session.get("update_items", [])
    
    if not update_items:
        await message.channel.send("❌ No items found in your reminder session. Please wait for the next reminder.")
        update_manager.end_session(user_id)
        return
    
    update_content = message.content.strip()
    
    if not update_content:
        await message.channel.send("❌ Please provide some content for your update.")
        return
    
    # If only one item, post the update directly
    if len(update_items) == 1:
        await post_update_to_github(message, update_manager, session, 0, update_content)
    
    else:
        # Multiple items, store the update and ask which item it's for
        update_manager.update_session(user_id, {
            "stage": "awaiting_item_selection_for_update",
            "pending_update_content": update_content
        })
        await send_item_selection_for_update(message, update_manager, session, update_content)


async def handle_item_selection(message: discord.Message, update_manager: GitHubUpdateManager, session: Dict):
    """Handle user selecting which item to update from multiple options."""
    user_id = message.author.id
    update_items = session.get("update_items", [])
    user_input = message.content.strip()
    
    # Try to parse the selection
    try:
        selection = int(user_input)
        if 1 <= selection <= len(update_items):
            selected_index = selection - 1
            selected_item = update_items[selected_index]
            
            await message.channel.send(
                f"🎯 **Selected:** {selected_item['repository']}#{selected_item['number']}\n"
                f"📝 **Title:** *{selected_item['title']}*\n\n"
                "💬 **Please send your update message** and I'll post it as a comment on GitHub for you!"
            )
            
            update_manager.update_session(user_id, {
                "stage": "awaiting_update_content",
                "selected_item_index": selected_index
            })
            return
            
    except ValueError:
        pass
    
    # Invalid selection
    await message.channel.send(
        f"❌ Please enter a valid number between 1 and {len(update_items)}.\n\n"
        f"Here are your options again:\n{update_manager.format_item_list(update_items, session.get('updated_items', []))}"
    )


async def handle_update_content(message: discord.Message, update_manager: GitHubUpdateManager, session: Dict):
    """Handle user providing the actual update content to post to GitHub."""
    user_id = message.author.id
    update_items = session.get("update_items", [])
    selected_index = session.get("selected_item_index")
    
    if selected_index is None or selected_index >= len(update_items):
        await message.channel.send("❌ Error: No item selected. Let me reset your session.")
        update_manager.end_session(user_id)
        return
    
    selected_item = update_items[selected_index]
    update_content = message.content.strip()
    
    if not update_content:
        await message.channel.send("❌ Please provide some content for your update.")
        return
    
    # Show typing indicator while posting to GitHub
    async with message.channel.typing():
        # Post the comment to GitHub
        success, result_message = await update_manager.post_github_comment(
            repository=selected_item["repository"],
            item_number=selected_item["number"],
            comment_body=f"Update from @{session.get('github_username', 'unknown')}: {update_content}",
            item_type=selected_item["type"]
        )
    
    if success:
        # Update the session to mark this item as updated
        updated_items = session.get("updated_items", [])
        updated_items.append(selected_index)
        
        await message.channel.send(f"{result_message}")
        
        # Check if there are more items to update
        remaining_items = [i for i, item in enumerate(update_items) if i not in updated_items]
        
        if remaining_items:
            await message.channel.send(
                f"Would you like to update another item? You have {len(remaining_items)} more items:\n\n"
                f"{update_manager.format_item_list(update_items, updated_items)}\n\n"
                "Reply with **yes** to continue or **no** to finish."
            )
            update_manager.update_session(user_id, {
                "stage": "awaiting_continue_choice",
                "updated_items": updated_items
            })
        else:
            # All items updated
            await message.channel.send(
                "🎉 Great! You've updated all your items. Thanks for keeping your GitHub activity up to date!"
            )
            update_manager.end_session(user_id)
    
    else:
        # Error posting to GitHub
        await message.channel.send(f"{result_message}\n\nWould you like to try again or update a different item?")
        # Stay in the same stage to allow retry


async def handle_continue_choice(message: discord.Message, update_manager: GitHubUpdateManager, session: Dict):
    """Handle user's choice of whether to continue updating more items."""
    user_id = message.author.id
    user_input = message.content.lower().strip()
    
    if user_input in ["yes", "y", "continue", "more", "next"]:
        # User wants to continue, show remaining items
        await send_item_selection_prompt(message, update_manager, session)
    
    elif user_input in ["no", "n", "done", "finish", "stop"]:
        # User is done
        updated_count = len(session.get("updated_items", []))
        await message.channel.send(
            f"✅ Perfect! You've updated {updated_count} item(s). "
            "Thanks for keeping your GitHub activity up to date!"
        )
        update_manager.end_session(user_id)
    
    else:
        # Unclear response
        await message.channel.send(
            "Please reply with **yes** to update another item or **no** to finish."
        )


async def send_item_selection_prompt(message: discord.Message, update_manager: GitHubUpdateManager, session: Dict):
    """Send a prompt for the user to select which item to update."""
    user_id = message.author.id
    update_items = session.get("update_items", [])
    updated_items = session.get("updated_items", [])
    
    # Show only remaining items
    remaining_items = [item for i, item in enumerate(update_items) if i not in updated_items]
    
    if not remaining_items:
        await message.channel.send("🎉 You've updated all your items! Great job!")
        update_manager.end_session(user_id)
        return
    
    await message.channel.send(
        f"📋 **You have {len(remaining_items)} item(s) from your reminder.** Which would you like to update?\n\n"
        f"{update_manager.format_item_list(update_items, updated_items)}\n\n"
        "💬 **Reply with the number** of the item you'd like to update:"
    )
    
    update_manager.update_session(user_id, {"stage": "awaiting_item_selection"})

async def handle_item_selection_for_update(message: discord.Message, update_manager: GitHubUpdateManager, session: Dict):
    """Handle user selecting which item their already-provided update is for."""
    update_items = session.get("update_items", [])
    pending_update = session.get("pending_update_content", "")
    user_input = message.content.strip()
    
    # Try to parse the selection
    try:
        selection = int(user_input)
        if 1 <= selection <= len(update_items):
            selected_index = selection - 1
            await post_update_to_github(message, update_manager, session, selected_index, pending_update)
            return
            
    except ValueError:
        pass
    
    # Invalid selection
    await message.channel.send(
        f"❌ Please enter a valid number between 1 and {len(update_items)}.\n\n"
        f"Here are your options again:\n{update_manager.format_item_list(update_items, session.get('updated_items', []))}"
    )


async def post_update_to_github(message: discord.Message, update_manager: GitHubUpdateManager, session: Dict, item_index: int, update_content: str):
    """Post the user's update to GitHub and handle the response."""
    user_id = message.author.id
    update_items = session.get("update_items", [])
    
    if item_index >= len(update_items):
        await message.channel.send("❌ Error: Invalid item index. Let me reset your session.")
        update_manager.end_session(user_id)
        return
    
    selected_item = update_items[item_index]
    
    # Show typing indicator while posting to GitHub
    async with message.channel.typing():
        # Post the comment to GitHub
        success, result_message = await update_manager.post_github_comment(
            repository=selected_item["repository"],
            item_number=selected_item["number"],
            comment_body=f"Update from @{session.get('github_username', 'unknown')}: {update_content}",
            item_type=selected_item["type"]
        )
    
    if success:
        # Update the session to mark this item as updated
        updated_items = session.get("updated_items", [])
        updated_items.append(item_index)
        
        await message.channel.send(f"{result_message}")
        
        # Check if there are more items to update
        remaining_items = [i for i, item in enumerate(update_items) if i not in updated_items]
        
        if remaining_items:
            await message.channel.send(
                f"📝 **Would you like to update another item?** You have {len(remaining_items)} more items:\n\n"
                f"{update_manager.format_item_list(update_items, updated_items)}\n\n"
                "💬 **Send your update message** for another item, or reply with **done** to finish."
            )
            update_manager.update_session(user_id, {
                "stage": "awaiting_next_update_or_done",
                "updated_items": updated_items
            })
        else:
            # All items updated
            await message.channel.send(
                "🎉 **Excellent!** You've updated all your items. Thanks for keeping your GitHub activity up to date!"
            )
            update_manager.end_session(user_id)
    
    else:
        # Error posting to GitHub
        await message.channel.send(f"{result_message}\n\n❓ Would you like to try again with a different message?")
        # Do not reset the stage. This allows the user to retry their last action
        # without losing the context of the conversation.


async def send_item_selection_for_update(message: discord.Message, update_manager: GitHubUpdateManager, session: Dict, update_content: str):
    """Send a prompt asking which item the update is for."""
    update_items = session.get("update_items", [])
    updated_items = session.get("updated_items", [])
    
    # Show a preview of their update
    preview = update_content[:100] + "..." if len(update_content) > 100 else update_content
    
    await message.channel.send(
        f"📝 **Got your update:** *\"{preview}\"*\n\n"
        f"📋 **Which item is this update for?**\n\n"
        f"{update_manager.format_item_list(update_items, updated_items)}\n\n"
        "💬 **Reply with the number** of the item:"
    )


async def handle_next_update_or_done(message: discord.Message, update_manager: GitHubUpdateManager, session: Dict):
    """Handle user providing another update or saying they're done."""
    user_id = message.author.id
    user_input = message.content.strip().lower()
    update_items = session.get("update_items", [])
    updated_items = session.get("updated_items", [])
    
    # Check if user is done
    if user_input in ["done", "finish", "stop", "no", "finished"]:
        updated_count = len(updated_items)
        await message.channel.send(
            f"✅ **Perfect!** You've updated {updated_count} item(s). "
            "Thanks for keeping your GitHub activity up to date!"
        )
        update_manager.end_session(user_id)
        return
    
    # Otherwise, treat the message as an update for another item
    update_content = message.content.strip()
    
    if not update_content:
        await message.channel.send("❌ Please provide some content for your update, or reply with **done** to finish.")
        return
    
    # Get remaining items
    remaining_items = [i for i, item in enumerate(update_items) if i not in updated_items]
    
    if not remaining_items:
        await message.channel.send("🎉 You've already updated all your items! Great job!")
        update_manager.end_session(user_id)
        return
    
    if len(remaining_items) == 1:
        # Only one item left, post directly
        await post_update_to_github(message, update_manager, session, remaining_items[0], update_content)
    else:
        # Multiple items remaining, ask which one
        update_manager.update_session(user_id, {
            "stage": "awaiting_item_selection_for_update",
            "pending_update_content": update_content
        })
        await send_item_selection_for_update(message, update_manager, session, update_content)
