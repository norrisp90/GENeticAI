import os
import chainlit as cl
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential
from azure.ai.agents.models import (
    AgentStreamEvent,
    MessageDeltaChunk,
    ThreadMessage,
    ThreadRun,
    RunStep,
    ListSortOrder
)
import logging
import asyncio
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
        self.thread = None
        self._initialized = False
        
    async def initialize(self):
        """Initialize Azure AI Project client and agent"""
        try:
            # Use async DefaultAzureCredential for authentication
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
                self.agent = await self.agents_client.get_agent(AGENT_ID)
            else:
                raise Exception("AZURE_AI_AGENT_ID environment variable is required")
            
            # Create a thread for this session
            self.thread = await self.agents_client.threads.create()
            
            self._initialized = True
            logger.info(f"Successfully initialized with agent: {self.agent.id} and thread: {self.thread.id}")
            return True
            
        except Exception as e:
            logger.error(f"Initialization failed: {e}")
            await self.cleanup()
            return False
    
    async def send_message_streaming(self, message: str, message_placeholder: cl.Message) -> str:
        """Send message to agent and stream response as it's generated"""
        try:
            # Check if clients, agent, and thread are properly initialized
            if not self._initialized or not self.agents_client:
                return "Error: Agents client not initialized"
            
            if not self.agent:
                return "Error: Agent not initialized"
                
            if not self.thread:
                return "Error: Thread not initialized"
            
            # Create user message in the existing thread
            await self.agents_client.messages.create(
                thread_id=self.thread.id,
                role="user",
                content=message
            )
            
            # Stream the agent response
            streaming_content = ""
            
            async with await self.agents_client.runs.stream(
                thread_id=self.thread.id, 
                agent_id=self.agent.id
            ) as stream:
                async for event_type, event_data, _ in stream:
                    
                    if isinstance(event_data, MessageDeltaChunk):
                        # Append the new text delta to our streaming content
                        if event_data.text:
                            streaming_content += event_data.text
                            # Update the UI with the accumulated content
                            message_placeholder.content = streaming_content
                            await message_placeholder.update()
                    
                    elif isinstance(event_data, ThreadMessage):
                        logger.debug(f"ThreadMessage created. ID: {event_data.id}, Status: {event_data.status}")
                    
                    elif isinstance(event_data, ThreadRun):
                        logger.debug(f"ThreadRun status: {event_data.status}")
                        if event_data.status == "failed":
                            error_msg = "Agent run failed"
                            message_placeholder.content = error_msg
                            await message_placeholder.update()
                            return error_msg
                    
                    elif isinstance(event_data, RunStep):
                        logger.debug(f"RunStep type: {event_data.type}, Status: {event_data.status}")
                    
                    elif event_type == AgentStreamEvent.ERROR:
                        error_msg = f"An error occurred: {event_data}"
                        logger.error(error_msg)
                        message_placeholder.content = error_msg
                        await message_placeholder.update()
                        return error_msg
                    
                    elif event_type == AgentStreamEvent.DONE:
                        logger.debug("Stream completed.")
                        break
            
            # Return the final accumulated content
            return streaming_content if streaming_content else "No response received"
                
        except Exception as e:
            logger.error(f"Message failed: {e}")
            error_response = f"Error: {str(e)}"
            message_placeholder.content = error_response
            await message_placeholder.update()
            return error_response
    
    async def cleanup(self):
        """Close the project client and reset state"""
        try:
            if self.project_client and self._initialized:
                await self.project_client.close()
                logger.info("Azure AI client closed successfully")
        except Exception as e:
            logger.warning(f"Error during cleanup: {e}")
        finally:
            self.project_client = None
            self.agents_client = None
            self.agent = None
            self.thread = None
            self._initialized = False

@cl.on_chat_start
async def start():
    """Initialize chat session"""
    await cl.Message(
        content="🤖 Initializing Azure AI Agent...",
        author="System"
    ).send()
    
    # Create a new agent instance for this session
    agent = AzureAIAgent()
    
    if await agent.initialize():
        if agent.agent and agent.thread:
            # Store the agent in user session
            cl.user_session.set("agent", agent)
            
            await cl.Message(
                content=f"✅ Connected to Azure AI Agent: {agent.agent.id}\nThread: {agent.thread.id}\n\nHow can I help you?",
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
    """Handle user messages with streaming response"""
    # Get the agent from user session
    agent = cl.user_session.get("agent")
    
    if not agent or not agent._initialized:
        await cl.Message(
            content="❌ Agent not initialized. Please restart the chat.",
            author="System"
        ).send()
        return
    
    # Create a placeholder message for streaming
    msg = cl.Message(
        content="🤖 *Thinking...*",
        author="Assistant"
    )
    await msg.send()
    
    # Send message to agent with streaming
    await agent.send_message_streaming(message.content, msg)

@cl.on_chat_end
async def end():
    """Clean up when chat ends"""
    # Get the agent from user session
    agent = cl.user_session.get("agent")
    
    if agent:
        await agent.cleanup()
        cl.user_session.set("agent", None)

if __name__ == "__main__":
    cl.run()