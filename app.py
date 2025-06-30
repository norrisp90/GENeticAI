import os
import chainlit as cl
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.ai.agents.models import ListSortOrder
import logging
import asyncio
import time
from typing import Optional

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
PROJECT_ENDPOINT = os.getenv("PROJECT_ENDPOINT")
AGENT_ID = os.getenv("AZURE_AI_AGENT_ID")

class AzureAIAgent:
    def __init__(self):
        self.project_client: Optional[AIProjectClient] = None
        self.agents_client = None
        self.agent = None
        
    def initialize(self):
        """Initialize Azure AI Project client and agent"""
        try:
            # Use DefaultAzureCredential for authentication
            credential = DefaultAzureCredential()
            
            # Initialize project client with endpoint
            if PROJECT_ENDPOINT:
                self.project_client = AIProjectClient(
                    endpoint=PROJECT_ENDPOINT,
                    credential=credential
                )
                # Get agents client from project client
                self.agents_client = self.project_client.agents
            else:
                raise Exception("PROJECT_ENDPOINT environment variable is required")
            
            # Get existing agent by ID
            if AGENT_ID:
                self.agent = self.agents_client.get_agent(AGENT_ID)
            else:
                raise Exception("AZURE_AI_AGENT_ID environment variable is required")
            
            logger.info(f"Successfully initialized with agent: {self.agent.id}")
            return True
            
        except Exception as e:
            logger.error(f"Initialization failed: {e}")
            return False
    
    async def send_message(self, message: str) -> str:
        """Send message to agent and get response"""
        try:
            # Check if clients and agent are properly initialized
            if not self.agents_client:
                return "Error: Agents client not initialized"
            
            if not self.agent:
                return "Error: Agent not initialized"
            
            # Create a thread
            thread = self.agents_client.threads.create()
            
            # Create user message in thread
            self.agents_client.messages.create(
                thread_id=thread.id,
                role="user",
                content=message
            )
            
            # Create and execute run
            run = self.agents_client.runs.create(
                thread_id=thread.id,
                agent_id=self.agent.id
            )
            
            # Poll for completion
            while run.status in ["queued", "in_progress", "requires_action"]:
                await asyncio.sleep(1)
                run = self.agents_client.runs.get(
                    thread_id=thread.id,
                    run_id=run.id
                )
            
            if run.status == "completed":
                # Get messages from thread
                messages = self.agents_client.messages.list(
                    thread_id=thread.id,
                    order=ListSortOrder.DESCENDING
                )
                
                # Find the latest assistant message
                for msg in messages:
                    if msg.role == "assistant" and msg.text_messages:
                        last_text = msg.text_messages[-1]
                        return last_text.text.value
                        
                return "No response content found."
            
            elif run.status == "failed":
                error_msg = run.last_error.message if run.last_error else "Unknown error"
                return f"Agent run failed: {error_msg}"
            
            else:
                return f"Agent run completed with status: {run.status}"
                
        except Exception as e:
            logger.error(f"Message failed: {e}")
            return f"Error: {str(e)}"

# Global agent instance
agent = AzureAIAgent()

@cl.on_chat_start
async def start():
    """Initialize chat session"""
    await cl.Message(
        content="🤖 Initializing Azure AI Agent...",
        author="System"
    ).send()
    
    if agent.initialize():
        if agent.agent:
            await cl.Message(
                content=f"✅ Connected to Azure AI Agent: {agent.agent.id}\n\nHow can I help you?",
                author="Assistant"
            ).send()
        else:
            await cl.Message(
                content="❌ Agent initialization incomplete.",
                author="System"
            ).send()
    else:
        await cl.Message(
            content="❌ Failed to connect to Azure AI Agent. Please check configuration.",
            author="System"
        ).send()

@cl.on_message
async def main(message: cl.Message):
    """Handle user messages"""
    if not agent.agents_client or not agent.agent:
        await cl.Message(
            content="❌ Agent not initialized. Please restart the chat.",
            author="System"
        ).send()
        return
    
    # Send message to agent
    response = await agent.send_message(message.content)
    
    # Send response back to user
    await cl.Message(
        content=response,
        author="Assistant"
    ).send()

if __name__ == "__main__":
    cl.run()