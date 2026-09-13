package lab_dream.exception;

/** 守门人的拒绝（HTTP 403 = "我知道你想干嘛，但我不同意"） */
public class GuardException extends RuntimeException {
    public GuardException(String message) {
        super(message);
    }
}
