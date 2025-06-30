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
        self.thread_id: Optional[str] = None  # Store thread ID separately
        self._initialized = False
        
    async def initialize(self, existing_thread_id: Optional[str] = None) -> bool:
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
            
            # Use existing thread or create new one
            if existing_thread_id:
                try:
                    # Try to get the existing thread
                    self.thread = await self.agents_client.threads.get(existing_thread_id)
                    self.thread_id = existing_thread_id
                    logger.info(f"Reconnected to existing thread: {self.thread_id}")
                except Exception as e:
                    logger.warning(f"Could not reconnect to thread {existing_thread_id}: {e}")
                    # Fall back to creating new thread
                    self.thread = await self.agents_client.threads.create()
                    self.thread_id = self.thread.id
            else:
                # Create a new thread for this session
                self.thread = await self.agents_client.threads.create()
                self.thread_id = self.thread.id
            
            self._initialized = True
            logger.info(f"Successfully initialized with agent: {self.agent.id} and thread: {self.thread_id}")
            return True
            
        except Exception as e:
            logger.error(f"Initialization failed: {e}")
            await self.cleanup()
            return False
    
    async def ensure_connected(self) -> bool:
        """Ensure the agent is connected, reconnect if needed"""
        if not self._initialized:
            logger.info("Agent not initialized, attempting to reconnect...")
            # Try to reconnect with existing thread ID
            stored_thread_id = cl.user_session.get("thread_id")
            success = await self.initialize(existing_thread_id=stored_thread_id)
            if success and self.thread_id:
                # Update stored thread ID
                cl.user_session.set("thread_id", self.thread_id)
            return success
        return True
    
    async def wake_up_agent(self) -> bool:
        """Send a wake-up message to the agent without displaying it to the user"""
        try:
            if not self._initialized:
                return False
            
            # Ensure we have all required components
            if not self.agents_client:
                logger.error("Agents client is None")
                return False
            
            if not self.thread:
                logger.error("Thread is None")
                return False
            
            if not self.agent:
                logger.error("Agent is None")
                return False
                
            # Send a simple wake-up message
            await self.agents_client.messages.create(
                thread_id=self.thread.id,
                role="user",
                content="Hello, are you ready to assist?"
            )
            
            # Create and execute run (don't stream, just wait for completion)
            run = await self.agents_client.runs.create(
                thread_id=self.thread.id,
                agent_id=self.agent.id
            )
            
            # Poll for completion (simple polling without streaming)
            max_attempts = 30  # 30 seconds timeout
            attempts = 0
            
            while run.status in ["queued", "in_progress", "requires_action"] and attempts < max_attempts:
                await asyncio.sleep(1)
                run = await self.agents_client.runs.get(
                    thread_id=self.thread.id,
                    run_id=run.id
                )
                attempts += 1
            
            if run.status == "completed":
                logger.info("Agent wake-up successful")
                return True
            else:
                logger.warning(f"Agent wake-up completed with status: {run.status}")
                return True  # Consider it successful even if not "completed"
                
        except Exception as e:
            logger.error(f"Agent wake-up failed: {e}")
            return False
    
    async def send_message_streaming(self, message: str, message_placeholder: cl.Message) -> str:
        """Send message to agent and stream response as it's generated"""
        try:
            # Ensure we're connected
            if not await self.ensure_connected():
                return "Error: Could not establish connection to Azure AI Agent"
            
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
            # Try to reconnect on error
            self._initialized = False
            error_response = f"Connection lost. Reconnecting... Error: {str(e)}"
            message_placeholder.content = error_response
            await message_placeholder.update()
            return error_response
    
    async def cleanup(self) -> None:
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
            # Don't reset thread_id here - keep it for reconnection
            self._initialized = False

async def get_or_create_agent() -> AzureAIAgent:
    """Get existing agent from session or create a new one"""
    agent = cl.user_session.get("agent")
    
    if not agent:
        agent = AzureAIAgent()
        cl.user_session.set("agent", agent)
    
    return agent

@cl.on_chat_start
async def start():
    """Initialize chat session"""
    init_msg = cl.Message(
        content="🤖 Initializing Azure AI Agent...",
        author="System"
    )
    await init_msg.send()
    
    # Create a new agent instance for this session
    agent = await get_or_create_agent()
    
    # Check if we have an existing thread ID
    existing_thread_id = cl.user_session.get("thread_id")
    
    if await agent.initialize(existing_thread_id=existing_thread_id):
        if agent.agent and agent.thread:
            # Store the thread ID for reconnection
            cl.user_session.set("thread_id", agent.thread_id)
            
            # Update initialization message
            init_msg.content = "🤖 Waking up agent..."
            await init_msg.update()
            
            # Wake up the agent automatically
            wake_up_success = await agent.wake_up_agent()
            
            # Prepare status message
            status_msg = f"✅ Connected to Azure AI Agent: {agent.agent.id}\n"
            if existing_thread_id and existing_thread_id == agent.thread_id:
                status_msg += f"Resumed conversation in thread: {agent.thread_id}\n\n"
                if wake_up_success:
                    status_msg += "Welcome back! Your conversation history is preserved and the agent is ready."
                else:
                    status_msg += "Welcome back! Your conversation history is preserved. (Agent wake-up had issues, but should still work)"
            else:
                status_msg += f"New conversation thread: {agent.thread_id}\n\n"
                if wake_up_success:
                    status_msg += "Agent is warmed up and ready! How can I help you?"
                else:
                    status_msg += "Agent initialized (wake-up had issues, but should still work). How can I help you?"
            
            # Update with final status
            init_msg.content = status_msg
            await init_msg.update()
            
        else:
            init_msg.content = "❌ Agent initialization incomplete."
            await init_msg.update()
    else:
        init_msg.content = "❌ Failed to connect to Azure AI Agent. Please check configuration."
        await init_msg.update()

@cl.on_message
async def main(message: cl.Message):
    """Handle user messages with streaming response"""
    # Get or create the agent
    agent = await get_or_create_agent()
    
    # Create a placeholder message for streaming
    msg = cl.Message(
        content="🤖 *Thinking...*",
        author="Assistant"
    )
    await msg.send()
    
    # Send message to agent with streaming (will auto-reconnect if needed)
    await agent.send_message_streaming(message.content, msg)

@cl.on_chat_end
async def end():
    """Clean up when chat ends"""
    # Get the agent from user session
    agent = cl.user_session.get("agent")
    
    if agent:
        await agent.cleanup()
        # Keep thread_id in session for potential reconnection
        # cl.user_session.set("thread_id", None)  # Uncomment to clear thread on end

if __name__ == "__main__":
    cl.run()