import os
import chainlit as cl
from azure.ai.projects import AIProjectClient
from azure.identity import ManagedIdentityCredential, DefaultAzureCredential
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
import asyncio
import traceback
from typing import Optional

# Azure AI Foundry configuration
PROJECT_CONNECTION_STRING = os.getenv("AZURE_AI_PROJECT_CONNECTION_STRING")
AGENT_ID = os.getenv("AZURE_AI_AGENT_ID")
# For system-assigned managed identity, these are typically not needed in the connection string
# but can be used for explicit client construction
AZURE_SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID")
AZURE_RESOURCE_GROUP = os.getenv("AZURE_RESOURCE_GROUP")
AZURE_PROJECT_NAME = os.getenv("AZURE_PROJECT_NAME")

class AzureAIAgent:
    def __init__(self):
        self.client = None
        self.agent = None
        self.thread = None
        
    def initialize(self):
        """Initialize the Azure AI Project client and agent using system-assigned managed identity"""
        print("=== Azure AI Foundry Initialization Debug Info ===")
        
        # Log environment variables (safely)
        print(f"PROJECT_CONNECTION_STRING set: {bool(PROJECT_CONNECTION_STRING)}")
        print(f"AGENT_ID: {AGENT_ID}")
        print(f"AZURE_SUBSCRIPTION_ID set: {bool(AZURE_SUBSCRIPTION_ID)}")
        print(f"AZURE_RESOURCE_GROUP: {AZURE_RESOURCE_GROUP}")
        print(f"AZURE_PROJECT_NAME: {AZURE_PROJECT_NAME}")
        
        try:
            # Test managed identity credential first
            print("\n--- Testing Managed Identity Credential ---")
            credential = ManagedIdentityCredential()
            
            # Try to get a token to test the credential
            try:
                # Use Azure Resource Manager scope to test credential
                token = credential.get_token("https://management.azure.com/.default")
                print(f"✅ Managed Identity credential working - Token expires: {token.expires_on}")
            except Exception as cred_error:
                print(f"❌ Managed Identity credential test failed: {str(cred_error)}")
                print(f"Error type: {type(cred_error).__name__}")
                
                # Try DefaultAzureCredential as fallback
                print("\n--- Trying DefaultAzureCredential as fallback ---")
                credential = DefaultAzureCredential()
                try:
                    token = credential.get_token("https://management.azure.com/.default")
                    print(f"✅ DefaultAzureCredential working - Token expires: {token.expires_on}")
                except Exception as default_cred_error:
                    print(f"❌ DefaultAzureCredential also failed: {str(default_cred_error)}")
                    raise Exception(f"Both credential types failed. Managed Identity: {cred_error}, Default: {default_cred_error}")
            
            # Initialize the client
            print("\n--- Initializing AI Project Client ---")
            if PROJECT_CONNECTION_STRING:
                print("Using PROJECT_CONNECTION_STRING...")
                self.client = AIProjectClient.from_connection_string(
                    conn_str=PROJECT_CONNECTION_STRING,
                    credential=credential
                )
                print("✅ Client created with connection string")
            elif all([AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, AZURE_PROJECT_NAME]):
                print("Using individual Azure parameters...")
                self.client = AIProjectClient(
                    credential=credential,
                    subscription_id=AZURE_SUBSCRIPTION_ID,
                    resource_group_name=AZURE_RESOURCE_GROUP,
                    project_name=AZURE_PROJECT_NAME
                )
                print("✅ Client created with individual parameters")
            else:
                raise Exception("Missing required configuration: either PROJECT_CONNECTION_STRING or all of (AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, AZURE_PROJECT_NAME)")
            
            # Test client by trying to list agents
            print("\n--- Testing Client Connection ---")
            try:
                agents = self.client.agents.list_agents()
                print(f"✅ Successfully connected - Found {len(agents.data)} agents")
                
                # Get the agent
                if AGENT_ID:
                    print(f"Looking for specified agent: {AGENT_ID}")
                    self.agent = self.client.agents.get_agent(AGENT_ID)
                    print(f"✅ Using specified agent: {AGENT_ID}")
                else:
                    if agents.data:
                        self.agent = agents.data[0]
                        print(f"✅ Using first available agent: {self.agent.id}")
                        cl.user_session.set("agent_id", self.agent.id)
                    else:
                        raise Exception("No agents found in the project")
                        
                print(f"✅ Successfully initialized Azure AI client with agent: {self.agent.id}")
                return True
                
            except HttpResponseError as http_error:
                print(f"❌ HTTP Error accessing agents: {http_error}")
                print(f"Status Code: {http_error.status_code}")
                print(f"Error Code: {http_error.error.code if hasattr(http_error, 'error') else 'N/A'}")
                print(f"Error Message: {http_error.message}")
                raise
            except Exception as client_error:
                print(f"❌ Error testing client connection: {str(client_error)}")
                print(f"Error type: {type(client_error).__name__}")
                raise
                
        except ClientAuthenticationError as auth_error:
            print(f"\n❌ Authentication Error: {str(auth_error)}")
            print("This typically means:")
            print("1. Managed Identity is not enabled on the App Service")
            print("2. Managed Identity doesn't have required permissions")
            print("3. The Azure AI project resource is not accessible")
            return False
            
        except HttpResponseError as http_error:
            print(f"\n❌ HTTP Response Error: {str(http_error)}")
            print(f"Status Code: {http_error.status_code}")
            if hasattr(http_error, 'error'):
                print(f"Error Code: {http_error.error.code}")
                print(f"Error Message: {http_error.error.message}")
            print("\nThis typically means:")
            print("1. Resource not found (check subscription ID, resource group, project name)")
            print("2. Insufficient permissions on the Azure AI project")
            print("3. Network connectivity issues")
            return False
            
        except Exception as e:
            print(f"\n❌ Unexpected Error: {str(e)}")
            print(f"Error Type: {type(e).__name__}")
            print(f"Full traceback:\n{traceback.format_exc()}")
            print("\nDetailed error information:")
            print("Make sure your system-assigned managed identity has the necessary permissions:")
            print("- Azure AI Developer role on the Azure AI project")
            print("- Contributor role on the resource group (if needed)")
            return False
    
    async def create_thread(self):
        """Create a new conversation thread"""
        try:
            self.thread = self.client.agents.create_thread()
            print(f"Created new thread: {self.thread.id}")
            return self.thread
        except Exception as e:
            print(f"Error creating thread: {str(e)}")
            print(f"Error type: {type(e).__name__}")
            print(f"Full traceback:\n{traceback.format_exc()}")
            return None
    
    async def send_message(self, message: str) -> str:
        """Send a message to the agent and get response"""
        try:
            if not self.thread:
                await self.create_thread()
            
            # Create a message in the thread
            message_obj = self.client.agents.create_message(
                thread_id=self.thread.id,
                role="user",
                content=message
            )
            
            # Create and run the assistant
            run = self.client.agents.create_run(
                thread_id=self.thread.id,
                assistant_id=self.agent.id
            )
            
            print(f"Created run: {run.id} with status: {run.status}")
            
            # Wait for the run to complete with timeout
            max_wait_time = 60  # 60 seconds timeout
            wait_time = 0
            
            while run.status in ["queued", "in_progress"] and wait_time < max_wait_time:
                await asyncio.sleep(1)
                wait_time += 1
                run = self.client.agents.get_run(
                    thread_id=self.thread.id,
                    run_id=run.id
                )
                print(f"Run status: {run.status}")
            
            if run.status == "completed":
                # Get the messages from the thread
                messages = self.client.agents.list_messages(
                    thread_id=self.thread.id
                )
                
                # Find the latest assistant message
                for msg in messages.data:
                    if msg.role == "assistant" and msg.created_at > message_obj.created_at:
                        return msg.content[0].text.value
                        
                return "I received your message but didn't generate a response."
                
            elif run.status == "failed":
                error_msg = getattr(run, 'last_error', {}).get('message', 'Unknown error')
                return f"I encountered an error processing your request: {error_msg}"
            elif wait_time >= max_wait_time:
                return "I'm taking longer than expected to respond. Please try again."
            else:
                return f"I'm currently {run.status}. Please try again in a moment."
            
        except Exception as e:
            print(f"Error sending message: {str(e)}")
            print(f"Error type: {type(e).__name__}")
            print(f"Full traceback:\n{traceback.format_exc()}")
            return f"I encountered an error: {str(e)}"

# Initialize the Azure AI agent
azure_agent = AzureAIAgent()

@cl.on_chat_start
async def start():
    """Initialize the chat session"""
    await cl.Message(
        content="🤖 Initializing Azure AI Foundry Agent with System-Assigned Managed Identity...",
        author="System"
    ).send()
    
    # Initialize Azure AI client
    if azure_agent.initialize():
        agent_info = f"Agent ID: {azure_agent.agent.id}" if azure_agent.agent else "Unknown agent"
        await cl.Message(
            content=f"✅ Connected to Azure AI Foundry using Managed Identity!\n\n{agent_info}\n\nHow can I help you today?",
            author="Assistant"
        ).send()
        
        # Store agent info in session
        cl.user_session.set("azure_agent", azure_agent)
        cl.user_session.set("initialized", True)
        
        # Create initial thread
        thread = await azure_agent.create_thread()
        if thread:
            cl.user_session.set("thread_id", thread.id)
            
    else:
        await cl.Message(
            content="""❌ Failed to connect to Azure AI Foundry using Managed Identity.

**Check the Azure App Service logs for detailed error information.**

**Common Issues & Solutions:**

🔧 **Managed Identity Issues:**
- Enable system-assigned managed identity in App Service → Identity
- Restart the App Service after enabling managed identity

🔐 **Permission Issues:**
- Assign **"Azure AI Developer"** role to the managed identity on the Azure AI project
- Assign **"Cognitive Services OpenAI User"** role if using OpenAI models
- Check role assignments in Azure AI project → Access Control (IAM)

🌐 **Configuration Issues:**
- Verify PROJECT_CONNECTION_STRING or individual Azure parameters
- Ensure the Azure AI project exists and is accessible
- Check network connectivity and firewall rules

📋 **To get detailed logs:**
1. Go to Azure App Service → Monitoring → Log stream
2. Check the console output for detailed error messages
3. Look for specific error codes and authentication failures

Please check the Azure App Service logs and configuration, and try again.""",
            author="System"
        ).send()
        cl.user_session.set("initialized", False)

@cl.on_message
async def main(message: cl.Message):
    """Handle incoming messages"""
    if not cl.user_session.get("initialized", False):
        await cl.Message(
            content="Please restart the chat to reinitialize the Azure AI connection.",
            author="System"
        ).send()
        return
    
    agent = cl.user_session.get("azure_agent")
    if not agent:
        await cl.Message(
            content="Azure AI agent not found. Please restart the chat.",
            author="System"
        ).send()
        return
    
    # Show typing indicator
    async with cl.Step(name="Processing with Azure AI Agent...") as step:
        step.input = message.content
        
        # Send message to Azure AI agent
        response = await agent.send_message(message.content)
        step.output = response
    
    # Send the response
    await cl.Message(
        content=response,
        author="Assistant"
    ).send()

@cl.on_chat_end
async def end():
    """Clean up when chat ends"""
    print("Chat session ended")

if __name__ == "__main__":
    cl.run()