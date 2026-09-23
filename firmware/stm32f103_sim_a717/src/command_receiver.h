/* ASCII command lines from the PC (spec v2 §26I). Replies only while stopped. */
#ifndef COMMAND_RECEIVER_H
#define COMMAND_RECEIVER_H
void command_receiver_init(void);
void command_receiver_poll(void);
#endif
