from openai import OpenAI
from typing import List, Optional
from config import OPENAI_API_KEY


class ConversationSummarizer:
    """AI-powered conversation summarizer using OpenAI for Discord transcript generation."""
    
    def __init__(self):
        """Initialize the ConversationSummarizer with OpenAI client."""
        self.client = None
        if OPENAI_API_KEY:
            self.client = OpenAI(api_key=OPENAI_API_KEY)
        else:
            print("⚠️ Warning: OPENAI_API_KEY not set. AI summarization will not work.")
    
    async def generate_conversation_summary(
        self, 
        formatted_conversation: str, 
        channel_name: str,
        real_names: List[str] = None
    ) -> Optional[str]:
        """
        Generate an AI-powered summary of a Discord conversation.
        
        Args:
            formatted_conversation: The formatted conversation text
            channel_name: Name of the Discord channel
            real_names: List of real names of participants
        
        Returns:
            AI-generated summary string, or None if generation fails
        """
        if not self.client:
            print("❌ Cannot generate summary: OpenAI client not configured")
            return None
        
        if not formatted_conversation.strip():
            print("❌ Cannot generate summary: No conversation content provided")
            return None
        
        try:
            # Create the AI prompt for summarization
            prompt = self._create_summarization_prompt(
                formatted_conversation, 
                channel_name, 
                real_names
            )
            
            print(f"🤖 Generating AI summary for #{channel_name} conversation...")
            
            # Use the ChatCompletion API for better control over the response
            response = self.client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a professional meeting notes assistant. Your job is to create clear, comprehensive summaries of team conversations that focus on general discussion topics, decisions made, and key points covered. Be concise."
                    },
                    {
                        "role": "user", 
                        "content": prompt
                    }
                ],
                max_tokens=1500,  # Allow for detailed summaries
                temperature=0.3,  # Lower temperature for more consistent, factual summaries
            )
            
            # Extract the summary from the response
            if response.choices and response.choices[0].message:
                summary = response.choices[0].message.content.strip()
                
                if summary:
                    print(f"✅ Generated AI summary ({len(summary)} characters)")
                    return summary
                else:
                    print("❌ AI returned empty summary")
                    return None
            else:
                print("❌ AI response format unexpected")
                return None
                
        except Exception as e:
            print(f"❌ Error generating AI summary: {e}")
            return None
    
    def _create_summarization_prompt(
        self, 
        conversation: str, 
        channel_name: str,
        real_names: List[str] = None
    ) -> str:
        """
        Create an effective AI prompt for conversation summarization.
        
        Args:
            conversation: The formatted conversation text
            channel_name: Name of the Discord channel
            real_names: List of real names of participants
        
        Returns:
            Formatted prompt string for the AI
        """
        participants_info = ""
        if real_names:
            participants_info = f"\n\nParticipants: {', '.join(real_names)}"
        
        prompt = f"""Channel: #{channel_name}

{participants_info}

Conversation:
{conversation}"""
        
        return prompt
    
    def format_summary_for_api(
        self, 
        summary: str, 
        channel_name: str, 
        message_count: int
    ) -> str:
        """
        Format the AI-generated summary for the Django API submission.
        
        Args:
            summary: Raw AI-generated summary
            channel_name: Name of the Discord channel
            message_count: Number of messages analyzed
        
        Returns:
            Formatted summary string ready for API submission
        """
        if not summary:
            return f"Conversation in #{channel_name} ({message_count} messages) - Summary generation failed"
        
        # Add some metadata to the summary
        formatted_summary = f"Discord conversation summary for #{channel_name}\n"
        formatted_summary += f"({message_count} messages analyzed)\n\n"
        formatted_summary += summary
        
        # Ensure the summary isn't too long for the API
        max_length = 4000  # Leave some buffer under the 4000 char API limit
        if len(formatted_summary) > max_length:
            # Truncate gracefully at a sentence boundary if possible
            truncated = formatted_summary[:max_length-3]
            last_period = truncated.rfind('.')
            if last_period > max_length - 200:  # If there's a sentence ending reasonably close
                formatted_summary = truncated[:last_period+1] + "..."
            else:
                formatted_summary = truncated + "..."
        
        return formatted_summary
