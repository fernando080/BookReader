
def get_new_chat_id(cursor, user_id, pdf_id):
    # It returns the chat_id + 1 of the last message of and specific user and pdf_id if it exists
    # else it returns 1
    cursor.execute("""
    SELECT TOP 1 CHAT_ID
    FROM CHATS
    WHERE USER_ID = ? 
    AND PDF_ID = ?
    AND IS_CHAT_CLOSED = 0
    ORDER BY CHAT_ID DESC
    """, user_id, pdf_id)
    row = cursor.fetchone()

    if row is None:
        return 1
    else:
        return row.CHAT_ID + 1