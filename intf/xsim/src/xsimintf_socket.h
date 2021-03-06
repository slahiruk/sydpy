#ifndef XSIMINTF_SOCKET_H
#define XSIMINTF_SOCKET_H

int socket_open(void);
int socket_send(const char* msg);
int socket_recv(char* msg, int max_len);
int socket_close(void);

#endif /* XSIMINTF_SOCKET_H */
