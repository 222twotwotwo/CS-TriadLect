package lab_dream.exception;

/** 剧情相关的业务异常（比如访问了不存在的章节） */
public class StoryException extends RuntimeException {
    public StoryException(String message) {
        super(message);
    }
}
