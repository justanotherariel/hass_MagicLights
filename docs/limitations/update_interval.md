# Update Interval of Lights

My initial inspiration for this project were awesome real-time lighting effects for everyone. This includes slow and smooth transitions, but also fast updates like the ones you get with a HUE HDMI Sync Box. Sadly, I have come to realize we are not quite there yet and would involve a lot more work on our backend.

## Issue Nr. 1
Zigbee messages remain longer in the network the larger it gets. Duh, you might say, but this is due to a limitation of the network topoloy zigbee uses in their standard configuration. Here, every node connects to every node in reach, creating a mesh and extending the network. So far so good. But if we send a message to the mesh, the each node will send each message it gets to every other node it is connected to. This ensures that each message is received by the recipient but also increases network traffic significantly. A more optimized way would be to pick a route and send the message only through a few nodes.


## Issue Nr. 2
But Ariel, you ask, isn't there already Phillips with HUE doing this?

To which I would respond, yes indeed, but they are a multi-billion dollar company. To make it short, they developed their own protocol for communictation between the lights running on top of Zigbee. This allows them to send messages to groups of lights with much less overhead and traffic for the network. "Great, so a company already has a solution, let's use it!", I hear you say.

Some Issues...
* This only works for HUE Lights. Phillips developed their HUE Lights firmware arround this streaming protocol and this is not implemented in any other zigbee light.
* This only works over the HUE Bridge, as no other alternative (Zigbee2Mqtt, ZHA, Deconz) has implemented this streaming protocol (and maybe requires firmware updates for the zigbee stick).
* Even with the HUE Integration, the HUE Enterainment API is not yet implemented in this HomeAssistant integration. This means light updates over this Integration will be subject to the same limitations as any other Zigbee light.

## Solutions
The easiest solution would be to limit ourselves to HUE for now and implement the HUE Entertainment API in our HomeAssistant HUE integration. For a lot of people, this would mean a second Zigbee bridge in thei cabinets again, but I guess that's the price we have to pay for proprietary technology.

This alone doesn't solve our problem though, as we will have to tell HomeAssistant (and in extension our HomeAssistants Integrations) how fast each light can update. This will be a non trivial problem, as each network type, network zone and device will have its own update interval. This will need some discussion on how to implement this properly.
